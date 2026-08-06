"""kt66 환경 시뮬레이터 — API + 수집 루프.

시설 계통(전력·냉방·소방·물리보안)은 가상이지만 **부하는 실제다**:
  · 2F/4F  docker stats 로 컨테이너 CPU 사용률을 읽는다
  · 3F     터널 너머 DGX Spark 의 Ollama 상태를 폴링해 GPU 사용률을 추정한다

경보는 syslog 로 Wazuh 매니저에 보낸다. 환경 이상이 **시스템 알림과 같은 SIEM 에**
들어오는 것이 중요하다 — 13주차의 "환경 → 시스템 연쇄"를 학생이 한 화면에서 본다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import socket
import time

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from model import FAULTS, Simulator

log = logging.getLogger("envsim")
logging.basicConfig(level=logging.INFO, format="[envsim] %(message)s")

ASSETS_PATH = os.getenv("ASSETS_PATH", "/app/assets.yaml")
DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")
SYSLOG_HOST = os.getenv("SYSLOG_HOST", "10.20.32.100")
SYSLOG_PORT = int(os.getenv("SYSLOG_PORT", "514"))
TICK_SEC = float(os.getenv("TICK_SEC", "5"))
API_KEY = os.getenv("API_KEY", "")

assets = yaml.safe_load(pathlib.Path(ASSETS_PATH).read_text(encoding="utf-8"))
sim = Simulator(assets)
app = FastAPI(title="kt66 환경 시뮬레이터", version="1.0")

_cpu_prev: dict[str, tuple[int, int]] = {}
_sent_events = 0


# ── 실제 부하 수집 ───────────────────────────────────────────────────
def _docker_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=DOCKER_SOCK),
                             base_url="http://docker", timeout=8.0)


async def collect_container_util() -> dict[str, float]:
    """컨테이너 CPU 사용률(0~1). docker 의 누적 카운터 차분으로 계산한다.

    one-shot + 동시 요청인 이유: stream=false 기본 동작은 docker 가 1초 간격 표본
    두 개를 뜰 때까지 기다린다. 자산 20개를 순차로 돌면 한 틱이 40초 가까이 걸리고,
    그러면 UPS 잔여 시간이 화면에서 멈춘 것처럼 보인다. 차분은 우리가 직접 하므로
    docker 가 기다려 줄 이유가 없다.
    """
    out: dict[str, float] = {}
    targets = {a["container"]: a["id"] for a in assets["it_assets"] if a.get("container")}
    if not targets:
        return out

    async def one(c: httpx.AsyncClient, cname: str, aid: str):
        try:
            r = await c.get(f"/containers/{cname}/stats",
                            params={"stream": "false", "one-shot": "true"})
            if r.status_code != 200:
                return
            cpu = r.json().get("cpu_stats", {})
            total = cpu.get("cpu_usage", {}).get("total_usage")
            system = cpu.get("system_cpu_usage")
            if total is None or system is None:
                return
            prev = _cpu_prev.get(cname)
            _cpu_prev[cname] = (total, system)
            if not prev:
                return
            d_total, d_sys = total - prev[0], system - prev[1]
            if d_sys > 0 and d_total >= 0:
                out[aid] = max(0.0, min(d_total / d_sys, 1.0))
        except Exception:
            return

    try:
        async with _docker_client() as c:
            await asyncio.gather(*(one(c, n, a) for n, a in targets.items()))
    except Exception as e:
        log.warning("docker 수집 실패: %s", e)
    return out


async def collect_gpu_util() -> dict[str, float]:
    """DGX Spark GPU 사용률.

    터널 너머라 nvidia-smi 를 직접 못 쓴다. Ollama 의 /api/ps 로 '모델이 GPU 에
    올라와 있는가'를 본다 — 정밀한 사용률은 아니지만 **실제 상태**이고, 부하를 걸면
    올라가고 빼면 내려간다. (정밀 측정이 필요해지면 DGX 에 exporter 를 둔다.)
    """
    out: dict[str, float] = {}
    for a in assets["it_assets"]:
        if not a.get("gpu") or not a.get("remote"):
            continue
        try:
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.get(f"http://{a['remote']}:11434/api/ps")
                if r.status_code != 200:
                    out[a["id"]] = 0.0
                    continue
                models = r.json().get("models") or []
                if not models:
                    out[a["id"]] = 0.05          # 유휴 — 켜져는 있다
                else:
                    # 올라온 모델 크기 합을 통합메모리(119GB) 대비로 환산
                    total = sum(float(m.get("size", 0)) for m in models)
                    out[a["id"]] = max(0.25, min(total / (119 * 1024 ** 3), 1.0))
        except Exception:
            out[a["id"]] = 0.0                   # 도달 불가 = 꺼짐으로 본다
    return out


# ── 경보 → SIEM ─────────────────────────────────────────────────────
def send_syslog(ev: dict):
    """Wazuh 매니저로 syslog 전송. 매니저가 디코딩해 알림으로 올린다."""
    global _sent_events
    pri = 11 if ev.get("kind") == "alarm" else 14
    payload = json.dumps({
        "kt66_envsim": True,
        "kind": ev.get("kind"),
        "alarm_id": ev.get("alarm_id"),
        "scope": ev.get("scope"),
        "level": ev.get("level"),
        "msg": ev.get("msg"),
    }, ensure_ascii=False)
    line = f"<{pri}>kt66-envsim: {payload}"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(line.encode("utf-8"), (SYSLOG_HOST, SYSLOG_PORT))
        s.close()
        _sent_events += 1
    except Exception as e:
        log.warning("syslog 전송 실패: %s", e)


# ── 루프 ────────────────────────────────────────────────────────────
async def loop():
    seen = 0
    while True:
        try:
            cu, gu = await asyncio.gather(collect_container_util(), collect_gpu_util())
            sim.tick({**cu, **gu})
            # 새 이벤트만 SIEM 으로
            for ev in sim.events[seen:]:
                if ev["kind"] in ("alarm", "clear", "inject"):
                    send_syslog(ev)
            seen = len(sim.events)
        except Exception as e:
            log.exception("tick 실패: %s", e)
        await asyncio.sleep(TICK_SEC)


@app.on_event("startup")
async def _start():
    log.info("자산 %d개 / 아일 %d개 / 경보규칙 %d개",
             len(assets["it_assets"]), len(sim.aisles), len(assets["alarms"]))
    log.info("SIEM syslog → %s:%d, 틱 %.1fs", SYSLOG_HOST, SYSLOG_PORT, TICK_SEC)
    asyncio.create_task(loop())


# ── API ─────────────────────────────────────────────────────────────
def _auth(key: str | None):
    if API_KEY and key != API_KEY:
        raise HTTPException(401, "API 키 불일치")


@app.get("/health")
def health():
    return {"ok": True, "uptime_ticks": len(sim.events), "syslog_sent": _sent_events}


@app.get("/state")
def state():
    """현재 환경 상태 전부. 시각화 UI 가 폴링한다."""
    return sim.state()


@app.get("/assets")
def get_assets():
    """자산 대장 원본 — UI 의 배치도 데이터 모델이자 CMDB 기준."""
    return assets


@app.get("/events")
def events(limit: int = 100):
    return {"events": sim.events[-limit:]}


@app.get("/alarms")
def alarms():
    return {"active": sorted(sim.active_alarms.values(), key=lambda r: -r["level"])}


@app.get("/faults")
def faults():
    return {"available": FAULTS, "active": {k: sorted(v) for k, v in sim.faults.items() if v}}


@app.post("/inject")
def inject(fault: str, target: str = "*", clear: bool = False, key: str | None = None):
    """강사용 시나리오 주입.

    예) POST /inject?fault=chiller_fail&target=chiller-01
        POST /inject?fault=utility_fail&target=utility-01
        POST /inject?fault=chiller_fail&target=chiller-01&clear=true
    """
    _auth(key)
    try:
        return sim.inject(fault, target, clear)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/shed")
def shed(group: str, restore: bool = False, key: str | None = None):
    """부하 차단 — ENV-03 에서 학생이 내리는 판단.

    예) POST /shed?group=ai-inference        추론 서비스를 끊는다
        POST /shed?group=ai-inference&restore=true
    """
    _auth(key)
    try:
        return sim.shed_load(group, restore)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/shed")
def shed_analysis():
    """부하 그룹별 소비 + 차단 시 영향 + 끊었을 때 잔여 시간."""
    return {"groups": sim.shed_analysis(),
            "ups_runtime_min": round(sim.ups_runtime_min, 1),
            "on_battery": sim.power.on_battery}


@app.post("/timescale")
def timescale(value: float, key: str | None = None):
    """시뮬레이션 시간 가속.

    유휴 상태의 랩은 발열이 5kW 남짓이라 냉동기를 죽여도 분당 0.2°C 밖에 안 오른다.
    한 교시 안에 전개시키려면 강사가 시간을 당겨야 한다. 다만 ENV-03(UPS 절체)은
    "11분 안에 무엇을 끌 것인가"가 실습의 본체이므로 ×1 로 둔다.
    """
    _auth(key)
    if not 0.1 <= value <= 120.0:
        raise HTTPException(400, "시간 배속은 0.1~120 사이여야 합니다")
    old, sim.time_scale = sim.time_scale, float(value)
    sim._event("info", f"시간 배속 변경: ×{old:g} → ×{value:g}")
    return {"time_scale": sim.time_scale}


@app.post("/reset")
def reset(key: str | None = None):
    _auth(key)
    return sim.clear_all()


@app.get("/")
def root():
    return JSONResponse({
        "name": "kt66 환경 시뮬레이터",
        "endpoints": ["/state", "/assets", "/alarms", "/events", "/faults", "/shed",
                      "POST /inject?fault=&target=", "POST /shed?group=",
                      "POST /timescale?value=", "POST /reset", "/health"],
        "faults": FAULTS,
    })
