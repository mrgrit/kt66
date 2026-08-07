"""kt66 고장 주입기 — IT 계통.

랩을 실제로 망가뜨리는 유일한 서비스다. 그래서 세 가지를 지킨다.

  ① **원복이 주입보다 중요하다.** state 형 주입은 반드시 짝이 되는 revert 를 갖고,
     TTL 이 지나면 자동으로 풀린다. 수업이 끝났는데 앞 조의 고장이 남아 있으면
     다음 조는 존재하지 않는 장애를 쫓게 된다.
  ② **화이트리스트만 받는다.** 카탈로그에 없는 id 는 실행되지 않고, 임의 명령을
     넣는 입구는 아예 없다. 대상도 주입마다 정해진 목록 안에서만 고른다.
  ③ **조종간은 못 끈다.** 관제 화면(noc)과 주입기 자신은 대상에서 제외한다.

주입·해제는 syslog 로 SIEM 에 나간다 — 강사의 조작도 사건이고, 학생이 보는 타임라인에
같이 남아야 상관분석이 성립한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import uuid

from fastapi import FastAPI, HTTPException

import catalog as cat
import dk

logging.basicConfig(level=logging.INFO, format="[injector] %(message)s")
log = logging.getLogger("injector")

API_KEY = os.getenv("API_KEY", "")
SYSLOG_HOST = os.getenv("SYSLOG_HOST", "10.20.32.100")
SYSLOG_PORT = int(os.getenv("SYSLOG_PORT", "514"))
SWEEP_SEC = 5.0

app = FastAPI(title="kt66 고장 주입기", version="1.0")

ACTIVE: dict[str, dict] = {}      # handle -> {id, target, params, payload, started, ttl}
EVENTS: list[dict] = []


def _auth(key: str | None):
    if API_KEY and key != API_KEY:
        raise HTTPException(401, "API 키가 필요합니다")


def _event(kind: str, msg: str, **extra):
    rec = {"ts": time.time(), "kind": kind, "msg": msg, **extra}
    EVENTS.append(rec)
    del EVENTS[:-400]
    log.info(msg)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(f"<134>kt66-injector {json.dumps({'kt66_injector': True, **rec}, ensure_ascii=False)}"
                 .encode(), (SYSLOG_HOST, SYSLOG_PORT))
        s.close()
    except Exception:
        pass


# ── 주입 ───────────────────────────────────────────────────────────
ACTION_SHOW_SEC = 180     # 1회성 주입이 '진행 중' 목록에 남아 있는 시간


async def _do_inject(inj: cat.Inj, target: str, params: dict, ttl: int) -> str:
    h = uuid.uuid4().hex[:8]
    # action 은 되돌릴 것이 없지만 목록에 영원히 남으면 안 된다 — 일정 시간 뒤 사라진다.
    if inj.kind == "action" and not ttl:
        ttl = ACTION_SHOW_SEC
    # apply 는 원복에 필요한 정보를 dict 로 돌려준다. 다만 대부분은 dk.sh 를 그대로
    # 반환하는 람다이고 dk.sh 는 **명령 출력(문자열)** 을 돌려준다 — 출력이 있는
    # 명령에서만 dict 가 아닌 값이 와서, 예전에 여기서 조용히 터졌다.
    res = await inj.apply(target, params, h)
    payload = {**params, **(res if isinstance(res, dict) else {}), "_h": h}
    ACTIVE[h] = {"handle": h, "id": inj.id, "domain": inj.domain, "name": inj.name,
                 "target": target, "params": params, "payload": payload,
                 "kind": inj.kind, "danger": inj.danger,
                 "started": time.time(), "ttl": ttl}
    _event("inject", f"주입: {inj.name} → {target}"
                     + (f" (자동해제 {ttl}초)" if inj.kind == "state" and ttl else ""),
           inj=inj.id, target=target, handle=h)
    return h


async def _do_revert(h: str, why: str = "수동 해제") -> bool:
    rec = ACTIVE.get(h)
    if not rec:
        return False
    inj = cat.BY_ID.get(rec["id"])
    try:
        if inj and inj.revert:
            await inj.revert(rec["target"], rec["payload"])
        _event("clear", f"{why}: {rec['name']} → {rec['target']}",
               inj=rec["id"], target=rec["target"], handle=h)
    except Exception as e:
        # 원복 실패는 조용히 넘어가면 안 된다. 다음 조가 그대로 물려받는다.
        _event("alarm", f"⚠ 원복 실패: {rec['name']} → {rec['target']} — {e}",
               inj=rec["id"], target=rec["target"], handle=h, level=12)
    finally:
        ACTIVE.pop(h, None)
    return True


async def _sweeper():
    """TTL 만료분을 자동 해제한다. 강사가 잊어도 랩은 스스로 원래대로 돌아간다."""
    while True:
        await asyncio.sleep(SWEEP_SEC)
        now = time.time()
        for h, r in list(ACTIVE.items()):
            if r["ttl"] and now - r["started"] >= r["ttl"]:
                # state 는 원복하고, action 은 목록에서만 내린다(_do_revert 가 알아서 구분).
                await _do_revert(h, "자동 해제(TTL 만료)" if r["kind"] == "state" else "목록에서 내림")


async def _startup_sweep():
    """기동 시 남아 있는 흔적을 걷어낸다. 앞 세션이 비정상 종료했을 수 있다."""
    cleaned = 0
    for c in cat.NETCAP:
        try:
            await dk.sh(c, "tc qdisc del dev eth0 root 2>/dev/null; "
                           "nft delete table inet kt66inj 2>/dev/null; "
                           "nft delete table inet kt66drift 2>/dev/null; true")
            cleaned += 1
        except Exception:
            pass
    for c in cat.SHELL_OK:
        try:
            await dk.sh(c, "pkill -9 -f kt66inj 2>/dev/null; rm -rf /var/tmp/kt66-inj-* "
                           "/var/tmp/kt66-io-* 2>/dev/null; true")
        except Exception:
            pass
    for ct in (await dk.containers()):
        name = (ct.get("Names") or ["/?"])[0].lstrip("/")
        if ct.get("State") == "paused" and name.startswith("kt66-"):
            try:
                await dk.unpause(name)
                cleaned += 1
            except Exception:
                pass
    log.info("기동 정리 완료 — %d개 대상", cleaned)


@app.on_event("startup")
async def _boot():
    await _startup_sweep()
    asyncio.create_task(_sweeper())
    log.info("주입 카탈로그 %d종 / 영역 %d개", len(cat.C), len(cat.DOMAINS))


# ── API ────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"ok": True, "catalog": len(cat.C), "active": len(ACTIVE)}


@app.get("/catalog")
async def get_catalog():
    """강사 패널이 한 번 받아 가는 정적 목록."""
    return {
        "domains": cat.DOMAINS,
        "protected": sorted(cat.PROTECTED),
        "injections": [{
            "id": i.id, "domain": i.domain, "name": i.name, "desc": i.desc,
            "teaches": i.teaches, "kind": i.kind, "danger": i.danger,
            "targets": [t for t in i.targets if t not in cat.PROTECTED],
            "params": i.params, "ttl": i.ttl, "scenarios": i.scenarios,
            "revertible": i.revert is not None,
        } for i in cat.C],
    }


@app.get("/active")
async def get_active():
    now = time.time()
    return {"active": [{
        **{k: v for k, v in r.items() if k != "payload"},
        "elapsed": round(now - r["started"]),
        "remaining": max(0, round(r["ttl"] - (now - r["started"]))) if r["ttl"] else None,
    } for r in ACTIVE.values()]}


@app.get("/events")
async def get_events(limit: int = 60):
    return {"events": EVENTS[-limit:]}


@app.post("/inject")
async def inject(id: str, target: str, ttl: int | None = None,
                 params: str | None = None, key: str | None = None):
    _auth(key)
    inj = cat.BY_ID.get(id)
    if not inj:
        raise HTTPException(404, f"알 수 없는 주입: {id}")
    if target in cat.PROTECTED:
        raise HTTPException(400, f"{target} 은 대상이 될 수 없습니다 — 조종간은 살아 있어야 합니다")
    if target not in inj.targets:
        raise HTTPException(400, f"{id} 는 {target} 에 걸 수 없습니다 "
                                 f"(가능: {', '.join(inj.targets)})")
    p = {}
    if params:
        try:
            p = json.loads(params)
        except Exception:
            raise HTTPException(400, "params 는 JSON 이어야 합니다")
    # 같은 주입이 같은 대상에 이미 걸려 있으면 겹쳐 걸지 않는다 — 원복이 꼬인다
    for h, r in ACTIVE.items():
        if r["id"] == id and r["target"] == target:
            raise HTTPException(409, f"이미 걸려 있습니다 (handle {h})")
    try:
        h = await _do_inject(inj, target, p, ttl if ttl is not None else inj.ttl)
    except Exception as e:
        _event("alarm", f"⚠ 주입 실패: {inj.name} → {target} — {e}", level=10)
        raise HTTPException(500, f"주입 실패: {e}")
    return {"handle": h, "id": id, "target": target, "kind": inj.kind}


@app.post("/clear")
async def clear(handle: str, key: str | None = None):
    _auth(key)
    if not await _do_revert(handle):
        raise HTTPException(404, "그런 주입이 없습니다")
    return {"cleared": handle}


@app.post("/clear_all")
async def clear_all(key: str | None = None):
    _auth(key)
    n = 0
    for h in list(ACTIVE):
        if await _do_revert(h, "전체 해제"):
            n += 1
    await _startup_sweep()
    return {"cleared": n}


@app.get("/")
async def root():
    return {"service": "kt66 injector", "catalog": len(cat.C),
            "endpoints": ["/catalog", "/active", "/events",
                          "POST /inject?id=&target=&ttl=&params=", "POST /clear?handle=",
                          "POST /clear_all", "/health"]}
