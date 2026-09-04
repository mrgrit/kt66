"""kt66 관제 화면(NOC) — 백엔드.

역할은 셋뿐이다. 스스로 상태를 만들지 않는다.

  ① 합성 — 흩어진 진실원천을 한 화면 분량으로 모은다
        envsim/assets.yaml  (건물·존·자산 대장)
        envsim /state       (전력·열·경보 — 실측 기반)
        agents/roster.yaml  (근무자 명단)
        docker.sock         (컨테이너가 실제로 살아 있는가)
  ② 치환 — ${INT_HOST}/${WEB_HOST} 를 배포된 실제 IP 로 바꾼다
  ③ 중계 — 브라우저의 고장 주입·부하 차단을 envsim 으로 넘긴다

시뮬레이션 로직은 여기 없다. 전부 envsim 에 있다. 관제 화면이 상태를 만들기
시작하면 화면과 실제가 갈라지고, 그 순간 교보재로서 못 쓰게 된다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import re
import time

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("noc")
logging.basicConfig(level=logging.INFO, format="[noc] %(message)s")

ENVSIM_URL = os.getenv("ENVSIM_URL", "http://10.20.60.10:8000")
INJECTOR_URL = os.getenv("INJECTOR_URL", "http://10.20.32.52:8000")
ROSTER_PATH = pathlib.Path(os.getenv("ROSTER_PATH", "/agents/roster.yaml"))
LOOPS_DIR = pathlib.Path(os.getenv("LOOPS_DIR", "/agents/loops"))
DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")
WEB_HOST = os.getenv("WEB_HOST", "192.168.12.100")
# 자산 대장 링크에 그대로 찍히는 "주소"다 — 바인딩 값이지 주소가 아닌 0.0.0.0 이 새어
# 들어오면 대장이 http://0.0.0.0:8020 을 가리킨다. 그때는 웹 진입 IP 로 대신한다.
# 옛 기본값(192.168.136.145)도 두지 않는다: el34 dummy NIC 이라 새 서버에서는 죽은 주소다.
INT_HOST = os.getenv("INT_HOST", "") or WEB_HOST
if INT_HOST in ("0.0.0.0", "::"):
    INT_HOST = WEB_HOST
API_KEY = os.getenv("API_KEY", "")
STATIC = pathlib.Path(__file__).parent / "static"

app = FastAPI(title="kt66 NOC", version="1.0")

_cache: dict[str, tuple[float, object]] = {}


def _sub(obj):
    """${INT_HOST}/${WEB_HOST} 치환. 대장에 IP 를 박아두면 배포마다 깨진다."""
    if isinstance(obj, str):
        return obj.replace("${INT_HOST}", INT_HOST).replace("${WEB_HOST}", WEB_HOST)
    if isinstance(obj, dict):
        return {k: _sub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sub(v) for v in obj]
    return obj


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


# ── envsim 중계 ─────────────────────────────────────────────────────
async def _relay(base: str, who: str, method: str, path: str, **kw):
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.request(method, f"{base}{path}", **kw)
            return r.status_code, r.json()
    except Exception as e:
        raise HTTPException(502, f"{who} 도달 실패: {e}")


async def _env(method: str, path: str, **kw):
    return await _relay(ENVSIM_URL, "envsim", method, path, **kw)


async def _inj(method: str, path: str, **kw):
    return await _relay(INJECTOR_URL, "injector", method, path, **kw)


# ── 근무자 명단 ─────────────────────────────────────────────────────
def load_roster() -> dict:
    if not ROSTER_PATH.exists():
        return {"workers": [], "error": f"{ROSTER_PATH} 없음"}
    data = yaml.safe_load(ROSTER_PATH.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults", {})
    loops = load_loops()
    for w in data.get("workers", []):
        w.setdefault("runtime", defaults.get("runtime", "bastion"))
        w.setdefault("autonomy", defaults.get("autonomy", "L1"))
        # 루프 요약을 붙여 준다 — 화면에서 "이 사람이 무슨 주기로 뭘 도는가"가 보여야 한다
        w["loop_detail"] = [loops[i] for i in w.get("loops", []) or [] if i in loops]
    return data


def load_loops() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not LOOPS_DIR.is_dir():
        return out
    for p in sorted(LOOPS_DIR.glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            lid = d.get("id", p.stem)
            out[lid] = {"id": lid, "name": d.get("name", lid),
                        "owner": d.get("owner"), "autonomy": d.get("autonomy"),
                        "cadence": d.get("cadence"), "runbook": d.get("runbook"),
                        "steps": len(d.get("steps") or []),
                        "gates": len(d.get("gates") or [])}
        except Exception as e:
            log.warning("루프 %s 읽기 실패: %s", p.name, e)
    return out


# ── 컨테이너 실제 상태 ──────────────────────────────────────────────
async def container_states() -> dict[str, dict]:
    """대장에 적힌 것과 실제로 떠 있는 것이 다를 수 있다. 그 차이를 화면에 보여준다."""
    out: dict[str, dict] = {}
    try:
        transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCK)
        async with httpx.AsyncClient(transport=transport, base_url="http://docker",
                                     timeout=8.0) as c:
            r = await c.get("/containers/json", params={"all": "true"})
            if r.status_code != 200:
                return out
            for ct in r.json():
                name = (ct.get("Names") or ["/?"])[0].lstrip("/")
                out[name] = {"state": ct.get("State"), "status": ct.get("Status"),
                             "image": ct.get("Image"),
                             "health": (ct.get("State") == "running")}
    except Exception as e:
        log.warning("docker 조회 실패: %s", e)
    return out


async def _reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        w.close()
        return True
    except Exception:
        return False


async def netglue_ok() -> bool | None:
    """웹 입구(fw DNAT)가 살아 있는가.

    호스트의 net.bridge.bridge-nf-call-iptables 가 1 로 되돌아가면 fw 의 DNAT
    **뒷다리**가 docker 의 per-network MASQUERADE 에 잡혀 게시 포트가 전부
    타임아웃한다. 그런데 그 값은 docker 데몬이 뜰 때마다 1 로 돌아간다
    (compose 의 netglue 서비스가 매 기동마다 되돌린다).

    sysctl 값을 직접 읽지 않는 이유: /proc/sys/net 은 **netns 별**이라 이 컨테이너가
    읽으면 자기 netns 값이 나온다 — 호스트 값이 아니다. 처음에 그렇게 짰다가
    글루가 멀쩡한데도 계속 '끊김'이 떴다. 그래서 값이 아니라 **성질**을 본다.

      fw DNAT 입구 안 되고  +  dmz 직접은 됨   → 글루 끊김 (False)
      둘 다 안 됨                              → 웹 자체가 죽은 것. 판단 보류 (None)

    조용히 죽는 것이 문제의 전부다 — 증상이 "WAF 가 로그를 안 남긴다"로 보여서
    학생은 WAF 를 판다. 그래서 관제 화면이 직접 두드려 본다.
    """
    hit = _cache.get("netglue")
    if hit and time.time() - hit[0] < 30.0:
        return hit[1]
    front, back = await asyncio.gather(_reachable("10.20.30.1", 8001),
                                       _reachable("10.20.32.80", 8001))
    val = True if front else (False if back else None)
    _cache["netglue"] = (time.time(), val)
    return val


# ── API ─────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    code, _ = await _env("GET", "/health")
    return {"ok": True, "envsim": code == 200, "netglue": await netglue_ok(),
            "hosts": {"int": INT_HOST, "web": WEB_HOST}}


@app.get("/api/layout")
async def layout():
    """건물 배치도 + 존 정의 + 자산 대장. 화면이 처음 한 번 받아 가는 정적 모델."""
    _, assets = await _env("GET", "/assets")
    return _sub(assets)


@app.get("/api/state")
async def state():
    """매 폴링마다 받아 가는 동적 상태. envsim 상태 + 컨테이너 생사."""
    (_, st), ct = await _env("GET", "/state"), await container_states()
    st["containers"] = ct
    st["netglue"] = await netglue_ok()
    return st


@app.get("/api/events")
async def events(limit: int = 60):
    _, d = await _env("GET", "/events", params={"limit": limit})
    return d


@app.get("/api/roster")
def roster():
    return _cached("roster", 5.0, load_roster)


@app.get("/api/faults")
async def faults():
    _, d = await _env("GET", "/faults")
    return d


@app.post("/api/inject")
async def inject(fault: str, target: str = "*", clear: bool = False):
    """강사 조작. 화면 밖으로 나가지 않고 여기서 시나리오를 넣는다."""
    code, d = await _env("POST", "/inject",
                         params={"fault": fault, "target": target,
                                 "clear": str(clear).lower(), "key": API_KEY})
    if code >= 400:
        raise HTTPException(code, d.get("detail", "주입 실패"))
    return d


@app.post("/api/shed")
async def shed(group: str, restore: bool = False):
    """학생 판단. ENV-03 에서 무엇을 끊을지 여기서 누른다."""
    code, d = await _env("POST", "/shed",
                         params={"group": group, "restore": str(restore).lower(),
                                 "key": API_KEY})
    if code >= 400:
        raise HTTPException(code, d.get("detail", "차단 실패"))
    return d


# ── IT 계통 주입기 중계 ────────────────────────────────────────────
# 시설(OT) 고장은 envsim, 나머지 38종은 injector 가 갖고 있다. 강사는 그 구분을
# 알 필요가 없으므로 관제 화면이 두 곳을 하나의 패널로 합친다.
@app.get("/api/inj/catalog")
async def inj_catalog():
    _, d = await _inj("GET", "/catalog")
    return d


@app.get("/api/inj/active")
async def inj_active():
    _, d = await _inj("GET", "/active")
    return d


@app.post("/api/inj/inject")
async def inj_inject(id: str, target: str, ttl: int | None = None, params: str | None = None):
    q = {"id": id, "target": target, "key": API_KEY}
    if ttl is not None:
        q["ttl"] = ttl
    if params:
        q["params"] = params
    code, d = await _inj("POST", "/inject", params=q)
    if code >= 400:
        raise HTTPException(code, d.get("detail", "주입 실패"))
    return d


@app.post("/api/inj/clear")
async def inj_clear(handle: str):
    code, d = await _inj("POST", "/clear", params={"handle": handle, "key": API_KEY})
    if code >= 400:
        raise HTTPException(code, d.get("detail", "해제 실패"))
    return d


@app.post("/api/inj/clear_all")
async def inj_clear_all():
    _, d = await _inj("POST", "/clear_all", params={"key": API_KEY})
    return d


@app.post("/api/timescale")
async def timescale(value: float):
    """시간 배속. 유휴 랩은 열이 천천히 오르므로 강사가 수업 속도에 맞춘다."""
    code, d = await _env("POST", "/timescale", params={"value": value, "key": API_KEY})
    if code >= 400:
        raise HTTPException(code, d.get("detail", "배속 변경 실패"))
    return d


@app.post("/api/reset")
async def reset():
    _, d = await _env("POST", "/reset", params={"key": API_KEY})
    _cache.clear()
    return d


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
