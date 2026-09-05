"""kt66 모델 운영(MLOps) — 서빙 · 수요 · 지표 · 배포.

학생이 하는 일은 하나다: **살아 있는 서비스의 모델을 고쳐서 유지한다.**

  ① 사용자가 계속 들어온다 (수요 생성기가 페르소나별로 요청을 만든다)
  ② 그중 일부가 실패한다 (현재 설정 때문에 — 잘리고, 지어내고, 엉뚱하게 거부한다)
  ③ 실패는 두 얼굴로 나타난다
       직접 — 티켓. 읽으면 안다
       간접 — 지표. 읽어내야 안다
  ④ 학생이 manifest 를 고쳐 새 버전을 만들고, 평가를 돌리고, 배포한다
  ⑤ 지표가 움직인다. 좋아졌는지 나빠졌는지는 다음 창에서 드러난다

DB 는 SQLite 한 개. 요청 로그와 티켓만 담는다. 모델 정의는 파일이다(registry).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import random
import sqlite3
import time

import demand
import evalset
import registry
import serving
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

API_KEY = os.getenv("API_KEY", "")
DB_PATH = os.getenv("DB_PATH", "/data/modelops.db")
HERE = pathlib.Path(__file__).parent
# 수요 생성 주기(초). 수업 속도에 맞춰 강사가 바꾼다.
DEMAND_SEC = float(os.getenv("DEMAND_SEC", "6"))

app = FastAPI(title="kt66 모델 운영", version="1.0")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
rng = random.Random(20260813)


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    pathlib.Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS req(
          id INTEGER PRIMARY KEY, at REAL, version TEXT, persona TEXT,
          prompt TEXT, answer TEXT, latency_ms INT, refused INT, truncated INT,
          retrieved INT, must_refuse INT, backend TEXT, err TEXT,
          -- 잘했는가. 규칙으로 매긴다(사람이 매기는 것이 아니다).
          --   거부해야 하는데 안 했다 / 답해야 하는데 거부했다 / 잘렸다 /
          --   근거가 필요한데 없었다  → 실패
          ok INT);
        CREATE INDEX IF NOT EXISTS req_at ON req(at);
        CREATE TABLE IF NOT EXISTS ticket(
          id INTEGER PRIMARY KEY, at REAL, title TEXT, body TEXT,
          hint TEXT, signal TEXT, state TEXT DEFAULT 'open', resolved_by TEXT);
        """)


def _auth(r: Request):
    if API_KEY and r.headers.get("x-api-key") != API_KEY:
        raise HTTPException(401, "API 키가 필요하다 — 화면 우측 상단에 서버 .env 의 API_KEY 값을 넣는다"
                            " (건드리지 않았으면 ccc-api-key-2026). LLM API 키가 아니다.")


# ── 수요 루프 ───────────────────────────────────────────────────────
def _score(persona: str, out: dict, must_refuse: bool) -> int:
    """이 응답이 쓸 만했는가. **규칙으로** 판정한다.

    사람이 매기면 실습 때마다 기준이 달라져 지표가 못 쓰게 된다. 규칙은
    evalset 의 네 가지와 같은 기준이다 — 그래야 평가와 운영 지표가 같은 말을 한다.
    """
    if must_refuse:
        return 1 if out["refused"] else 0
    if out["refused"]:
        return 0                      # 거부하면 안 되는 것을 거부했다
    if out.get("truncated"):
        return 0                      # 뒤를 못 봤으면 답이 맞을 리 없다
    p = demand.PERSONAS.get(persona, {})
    if p.get("needs_knowledge") and not out.get("retrieved_chars"):
        return 0                      # 근거가 필요한데 없이 답했다 = 지어낸 것
    return 1


async def demand_loop():
    while True:
        await asyncio.sleep(DEMAND_SEC)
        try:
            v = registry.active()
            if not v:
                continue
            m = registry.load(v)
            r = demand.make_request(rng)
            out = await serving.generate(m, r["prompt"])
            ok = _score(r["persona"], out, r["must_refuse"])
            with db() as c:
                c.execute(
                    "INSERT INTO req(at,version,persona,prompt,answer,latency_ms,"
                    "refused,truncated,retrieved,must_refuse,backend,err,ok) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), v, r["persona"], r["prompt"][:4000], out["text"][:4000],
                     out["latency_ms"], int(out["refused"]), int(out.get("truncated", 0)),
                     int(out.get("retrieved_chars", 0)), int(r["must_refuse"]),
                     out["backend"], out.get("error"), ok))
            await maybe_ticket()
        except Exception:
            # 수요 루프가 죽으면 실습이 통째로 멈춘다. 한 건 실패는 넘어간다.
            continue


async def maybe_ticket():
    """간접 신호가 임계를 넘으면 **직접 티켓**으로 승격한다.

    이게 이 서비스의 핵심 장치다. 학생이 지표를 안 보고 있어도, 문제가 충분히
    쌓이면 결국 티켓으로 온다 — 실무와 같다. 다만 그때는 이미 사용자가 겪은 뒤다.
    지표를 먼저 본 학생과 티켓을 기다린 학생의 차이가 여기서 벌어진다.
    """
    with db() as c:
        rows = c.execute("SELECT * FROM req ORDER BY id DESC LIMIT 40").fetchall()
        open_n = c.execute("SELECT COUNT(*) n FROM ticket WHERE state='open'").fetchone()["n"]
    if len(rows) < 20 or open_n >= 4:
        return
    sig = None
    if sum(r["truncated"] for r in rows) >= 6:
        sig = "context_tokens"
    elif sum(1 for r in rows if r["refused"] and not r["must_refuse"]) >= 4:
        sig = "refuse_patterns"
    elif sum(1 for r in rows if not r["retrieved"] and not r["refused"]) >= 14:
        sig = "retrieval"
    elif sorted(r["latency_ms"] for r in rows)[int(len(rows) * 0.95) - 1] > 2500:
        sig = "max_tokens"
    if not sig:
        return
    with db() as c:
        dup = c.execute("SELECT 1 FROM ticket WHERE signal=? AND state='open'",
                        (sig,)).fetchone()
        if dup:
            return
        t = demand.ticket_from_signal(rng, sig)
        c.execute("INSERT INTO ticket(at,title,body,hint,signal) VALUES(?,?,?,?,?)",
                  (time.time(), t["title"], t["body"], json.dumps(t["hint"]), sig))


@app.on_event("startup")
async def _start():
    init()
    app.state.task = asyncio.create_task(demand_loop())


@app.on_event("shutdown")
async def _stop():
    t = getattr(app.state, "task", None)
    if t:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


# ── API ─────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"ok": True, "active": registry.active(), "versions": registry.versions(),
            "backend": "ollama" if serving.OLLAMA_URL else "mock"}


@app.get("/api/state")
def state(window: int = 300):
    """화면이 폴링해 가는 한 덩어리. 지표는 **활성 버전 기준 최근 창**이다."""
    since = time.time() - window
    with db() as c:
        rows = c.execute("SELECT * FROM req WHERE at>=? ORDER BY id DESC",
                         (since,)).fetchall()
        recent = c.execute("SELECT * FROM req ORDER BY id DESC LIMIT 25").fetchall()
        tickets = c.execute("SELECT * FROM ticket ORDER BY state='closed', id DESC "
                            "LIMIT 20").fetchall()
    return {"active": registry.active(), "versions": registry.versions(),
            "metrics": _metrics(rows), "by_version": _by_version(rows),
            "recent": [_req(r) for r in recent],
            "tickets": [dict(t) | {"hint": json.loads(t["hint"] or "[]")} for t in tickets],
            "window": window}


def _req(r) -> dict:
    return {"id": r["id"], "at": r["at"], "persona": r["persona"], "version": r["version"],
            "prompt": r["prompt"][:160], "answer": r["answer"][:200],
            "latency_ms": r["latency_ms"], "refused": r["refused"],
            "truncated": r["truncated"], "retrieved": r["retrieved"], "ok": r["ok"],
            "must_refuse": r["must_refuse"], "backend": r["backend"]}


def _metrics(rows) -> dict:
    if not rows:
        return {"n": 0}
    lat = sorted(r["latency_ms"] for r in rows)
    n = len(rows)
    bad_refuse = sum(1 for r in rows if r["refused"] and not r["must_refuse"])
    miss_refuse = sum(1 for r in rows if r["must_refuse"] and not r["refused"])
    return {
        "n": n,
        "p50": lat[int(n * 0.50) - 1], "p95": lat[int(n * 0.95) - 1],
        "ok_rate": round(sum(r["ok"] for r in rows) / n * 100, 1),
        "truncated": sum(r["truncated"] for r in rows),
        "ungrounded": sum(1 for r in rows if not r["retrieved"] and not r["refused"]),
        "over_refuse": bad_refuse,      # 막지 말아야 할 것을 막았다
        "leak": miss_refuse,            # 막아야 할 것을 안 막았다 ← 가장 나쁘다
        "errors": sum(1 for r in rows if r["err"]),
    }


def _by_version(rows) -> list[dict]:
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["version"], []).append(r)
    return [{"version": v, **_metrics(rs)} for v, rs in sorted(out.items())]


@app.get("/api/version/{v}")
def get_version(v: str):
    try:
        return registry.load(v)
    except registry.RegistryError as e:
        raise HTTPException(404, str(e))


@app.post("/api/version/{v}")
async def put_version(v: str, r: Request, body: dict = Body(...)):
    _auth(r)
    try:
        return registry.save(v, body.get("manifest") or {}, body.get("knowledge"))
    except registry.RegistryError as e:
        raise HTTPException(400, str(e))


@app.post("/api/deploy/{v}")
async def do_deploy(v: str, r: Request):
    _auth(r)
    try:
        return {"active": registry.deploy(v)}
    except registry.RegistryError as e:
        raise HTTPException(400, str(e))


@app.post("/api/eval/{v}")
async def run_eval(v: str, r: Request):
    """배포 전에 돌린다. 결과는 저장하지 않는다 — 지금 이 manifest 의 성적이다."""
    _auth(r)
    try:
        m = registry.load(v)
    except registry.RegistryError as e:
        raise HTTPException(404, str(e))
    items = []
    for it in evalset.EVALS:
        out = await serving.generate(m, it["prompt"])
        ok, why = evalset.judge(it, out)
        items.append({"id": it["id"], "kind": it["kind"], "why": it["why"],
                      "passed": ok, "detail": why,
                      "latency_ms": out["latency_ms"]})
    return {"version": v, "passed": sum(i["passed"] for i in items),
            "total": len(items), "items": items}


@app.post("/api/ask")
async def ask(r: Request, body: dict = Body(...)):
    """학생이 직접 물어본다. 로그에 남는다 — 손으로 친 것도 트래픽이다."""
    m = registry.load(registry.active())
    out = await serving.generate(m, body.get("prompt", ""))
    with db() as c:
        c.execute("INSERT INTO req(at,version,persona,prompt,answer,latency_ms,"
                  "refused,truncated,retrieved,must_refuse,backend,err,ok) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (time.time(), m["version"], "학생", body.get("prompt", "")[:4000],
                   out["text"][:4000], out["latency_ms"], int(out["refused"]),
                   int(out.get("truncated", 0)), int(out.get("retrieved_chars", 0)),
                   0, out["backend"], out.get("error"),
                   _score("학생", out, False)))
    return out


@app.post("/api/ticket/{tid}/close")
async def close_ticket(tid: int, r: Request, body: dict = Body(default={})):
    _auth(r)
    with db() as c:
        cur = c.execute("UPDATE ticket SET state='closed', resolved_by=? WHERE id=?",
                        (body.get("by") or registry.active(), tid))
        if not cur.rowcount:
            raise HTTPException(404, "그런 티켓이 없다")
    return {"closed": tid}


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "templates" / "modelops.html").read_text(encoding="utf-8")
