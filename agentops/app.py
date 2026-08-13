"""kt66 에이전트 운영 콘솔 — 회사를 세우고 근무자를 조정한다.

    회사(비전·목표) → 부서(미션) → 팀(KPI·경험그래프) → 근무자(페르소나·런타임·모델)
                                                        → 일하는 방식(하네스·루프) → 적용

── 이 화면의 규칙 ─────────────────────────────────────────────────
① **파일이 진실원천이다.** 이 서비스는 DB 를 갖지 않는다. agents/ 의 YAML 과
   마크다운을 직접 읽고 쓴다. 그래야 학생이 웹으로 바꾼 것과 셸에서 바꾼 것이
   같은 것이 되고, git diff 로 자기가 무엇을 바꿨는지 볼 수 있다.
② **저장 전에 검증한다.** YAML 이 깨지거나 상호 참조가 끊기면 거부한다.
   학생이 실습 중에 조직을 망가뜨리고 복구하지 못하는 상황을 만들지 않는다.
③ **저장 전에 백업한다.** .bak/ 에 타임스탬프로 남긴다. 되돌리기 경로가 없는
   조작을 학생에게 시키면서 우리가 그 원칙을 어길 수는 없다.
④ **단계는 스토리다.** 여섯 단계가 각각 하나의 질문에 답한다. 한 화면에 전부
   늘어놓으면 학생은 무엇부터 봐야 할지 모른다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).parent
AGENTS = Path(os.environ.get("AGENTS_DIR", "/agents"))
BAK = AGENTS / ".bak"
API_KEY = os.environ.get("API_KEY", "ccc-api-key-2026")

app = FastAPI(title="kt66 agentops", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=str(HERE / "templates"))

# 편집 가능한 단일 파일들. 여기 없는 경로는 쓰기를 거부한다(경로 탈출 방지).
FILES = {
    "company": "company.yaml",
    "departments": "departments.yaml",
    "teams": "teams.yaml",
    "roster": "roster.yaml",
    "harness": "harness.yaml",
}
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")

# ── 주석을 지키는 YAML ──────────────────────────────────────────────
# safe_dump 로 다시 쓰면 **주석이 전부 사라진다.** 이 파일들의 주석에는 '왜' 가
# 적혀 있고 그것이 교보재의 절반이다. 근무자 하나 추가했다고 그게 날아가면
# 학생은 자기가 무엇을 잃었는지도 모른다.
# 그래서 구조 편집(추가·삭제·필드 변경)은 ruamel 의 round-trip 으로 한다 —
# 건드린 줄만 바뀌고 나머지 서식과 주석은 그대로 남는다.
_rt = YAML()
_rt.preserve_quotes = True
_rt.width = 4096                    # 긴 한국어 줄이 임의로 접히지 않게
_rt.indent(mapping=2, sequence=4, offset=2)


def _load_rt(name: str):
    """round-trip 로드. 저장할 객체는 반드시 이것으로 읽어야 주석이 산다."""
    p = AGENTS / FILES[name]
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        return _rt.load(f) or {}


def _dump_rt(name: str, data) -> None:
    import io
    buf = io.StringIO()
    _rt.dump(data, buf)
    _write_text(AGENTS / FILES[name], buf.getvalue())


# ── 읽기 ────────────────────────────────────────────────────────────
def _read_yaml(name: str) -> dict:
    p = AGENTS / FILES[name]
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _read_graph() -> dict:
    p = AGENTS / "graph" / "experience.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"nodes": [], "edges": []}


def _personas() -> list[str]:
    return sorted(p.stem for p in (AGENTS / "personas").glob("*.md"))


def _loops() -> list[str]:
    return sorted(p.stem for p in (AGENTS / "loops").glob("*.yaml"))


# ── 검증 ────────────────────────────────────────────────────────────
def validate_all(over: dict | None = None) -> list[str]:
    """조직 전체의 상호 참조를 확인한다. over 로 저장 예정 내용을 미리 끼워 본다.

    저장한 뒤에 깨진 것을 발견하면 이미 늦다 — 학생은 무엇이 깨졌는지 모른 채
    다음 단계로 간다. 그래서 **쓰기 전에** 전체를 조립해 본다.
    """
    over = over or {}
    g = lambda k: over.get(k, _read_yaml(k))       # noqa: E731
    company, depts, teams, roster = g("company"), g("departments"), g("teams"), g("roster")
    err: list[str] = []

    goals = {x["id"] for x in company.get("company", {}).get("goals", [])}
    dept_list = depts.get("departments", [])
    team_list = teams.get("teams", [])
    team_ids = {t["id"] for t in team_list}
    workers = roster.get("workers", [])
    worker_ids = {w["id"] for w in workers}
    models = set(roster.get("models", {}))
    runtimes = set(roster.get("runtimes", {}))

    for d in dept_list:
        for gid in d.get("owns_goals", []):
            if gid not in goals:
                err.append(f"부서 {d['id']} 가 없는 목표를 참조한다: {gid}")
        for tid in d.get("teams", []):
            if tid not in team_ids:
                err.append(f"부서 {d['id']} 가 없는 팀을 참조한다: {tid}")
    dept_ids = {d["id"] for d in dept_list}
    for t in team_list:
        if t.get("department") not in dept_ids:
            err.append(f"팀 {t['id']} 의 부서가 없다: {t.get('department')}")
        for m in t.get("members", []):
            if m not in worker_ids:
                err.append(f"팀 {t['id']} 가 없는 근무자를 참조한다: {m}")
    declared = {tid for d in dept_list for tid in d.get("teams", [])}
    for tid in team_ids - declared:
        err.append(f"팀 {tid} 가 어느 부서에도 속하지 않는다")

    personas, loops = set(_personas()), set(_loops())
    for w in workers:
        if not ID_RE.match(w.get("id", "")):
            err.append(f"근무자 id 형식이 잘못됐다: {w.get('id')!r} (소문자·숫자·하이픈)")
        if w.get("team") and w["team"] not in team_ids:
            err.append(f"근무자 {w['id']} 의 팀이 없다: {w['team']}")
        if w.get("model") and w["model"] not in models:
            err.append(f"근무자 {w['id']} 의 모델이 카탈로그에 없다: {w['model']}")
        if w.get("runtime") and w["runtime"] not in runtimes:
            err.append(f"근무자 {w['id']} 의 런타임이 없다: {w['runtime']}")
        if w["id"] not in personas:
            err.append(f"근무자 {w['id']} 의 페르소나 파일이 없다 (personas/{w['id']}.md)")
        for lp in w.get("loops", []):
            if lp not in loops:
                err.append(f"근무자 {w['id']} 가 없는 루프를 참조한다: {lp}")
        # 자율성 L3 는 런북이 있는 작업에만 — 회사 규칙이다
        if w.get("autonomy") == "L3" and not w.get("loops"):
            err.append(f"근무자 {w['id']} 가 L3(무인)인데 등록된 루프가 없다")
    for m in worker_ids - {m for t in team_list for m in t.get("members", [])}:
        err.append(f"근무자 {m} 가 어느 팀에도 속하지 않는다")
    return err


# ── 쓰기 ────────────────────────────────────────────────────────────
def _backup(p: Path) -> None:
    if not p.exists():
        return
    BAK.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(p, BAK / f"{p.name}.{stamp}")
    # 무한히 쌓이면 디스크를 먹는다. 파일당 최근 20개만 남긴다.
    keep = sorted(BAK.glob(f"{p.name}.*"))[:-20]
    for old in keep:
        old.unlink(missing_ok=True)


def _write_text(p: Path, text: str) -> None:
    _backup(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)                     # 원자적 교체 — 반쯤 쓰인 파일을 남기지 않는다


def _auth(key: str | None) -> None:
    if key != API_KEY:
        raise HTTPException(401, "API 키가 필요하다")


# ── API: 조회 ───────────────────────────────────────────────────────
@app.get("/api/org")
def org():
    """여섯 단계가 전부 쓰는 한 덩어리. 화면이 여러 번 왕복하지 않게 한 번에 준다."""
    roster = _read_yaml("roster")
    graph = _read_graph()
    return {
        "company": _read_yaml("company"),
        "departments": _read_yaml("departments"),
        "teams": _read_yaml("teams"),
        "roster": roster,
        "harness": _read_yaml("harness"),
        "graph": {"nodes": graph.get("nodes", []), "edges": graph.get("edges", []),
                  "open_questions": graph.get("open_questions", [])},
        "personas": _personas(),
        "loops": _loops(),
        "errors": validate_all(),
    }


@app.get("/api/file/{name}", response_class=PlainTextResponse)
def get_file(name: str):
    """원문 그대로. 학생이 주석까지 읽어야 한다 — 주석에 '왜'가 적혀 있다."""
    if name in FILES:
        p = AGENTS / FILES[name]
    elif name.startswith("persona:"):
        p = AGENTS / "personas" / f"{name[8:]}.md"
    elif name.startswith("loop:"):
        p = AGENTS / "loops" / f"{name[5:]}.yaml"
    elif name == "graph":
        p = AGENTS / "graph" / "experience.json"
    else:
        raise HTTPException(404, "알 수 없는 파일이다")
    if not p.exists():
        raise HTTPException(404, f"파일이 없다: {p.name}")
    return p.read_text(encoding="utf-8")


# ── API: 저장 ───────────────────────────────────────────────────────
@app.post("/api/file/{name}")
def put_file(name: str, key: str = "", body: dict = Body(...)):
    """원문 저장. 파싱해 보고 조직 정합성까지 확인한 뒤에만 쓴다."""
    _auth(key)
    text = body.get("text", "")
    if name in FILES:
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise HTTPException(400, f"YAML 문법 오류: {e}") from e
        if not isinstance(parsed, dict):
            raise HTTPException(400, "최상위가 매핑이 아니다")
        errs = validate_all({name: parsed})
        if errs:
            raise HTTPException(400, "조직 정합성 오류:\n" + "\n".join(f"· {e}" for e in errs))
        _write_text(AGENTS / FILES[name], text)
    elif name.startswith("persona:"):
        pid = name[8:]
        if not ID_RE.match(pid):
            raise HTTPException(400, "페르소나 id 형식이 잘못됐다")
        if not text.lstrip().startswith("---"):
            raise HTTPException(400, "페르소나는 --- 프런트매터로 시작해야 한다 (description·model·tools)")
        _write_text(AGENTS / "personas" / f"{pid}.md", text)
    elif name.startswith("loop:"):
        lid = name[5:]
        if not ID_RE.match(lid):
            raise HTTPException(400, "루프 id 형식이 잘못됐다")
        try:
            d = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise HTTPException(400, f"YAML 문법 오류: {e}") from e
        for k in ("id", "owner", "steps"):
            if k not in (d or {}):
                raise HTTPException(400, f"루프에 {k} 가 없다")
        _write_text(AGENTS / "loops" / f"{lid}.yaml", text)
    elif name == "graph":
        try:
            g = json.loads(text)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 문법 오류: {e}") from e
        ids = {n["id"] for n in g.get("nodes", [])}
        bad = [f"{e['from']}→{e['to']}" for e in g.get("edges", [])
               if e["from"] not in ids or e["to"] not in ids]
        if bad:
            raise HTTPException(400, "끊긴 edge: " + ", ".join(bad[:5]))
        noprov = [f"{e['from']}→{e['to']}" for e in g.get("edges", []) if not e.get("source")]
        if noprov:
            raise HTTPException(
                400, "출처(source) 없는 edge 는 저장할 수 없다 — 추측을 사실로 저장하면 "
                     "다음 판단이 오염된다: " + ", ".join(noprov[:5]))
        _write_text(AGENTS / "graph" / "experience.json", text)
    else:
        raise HTTPException(404, "알 수 없는 파일이다")
    return {"ok": True, "errors": validate_all()}


# ── API: 근무자 추가·삭제 ───────────────────────────────────────────
PERSONA_TEMPLATE = """---
description: "{name}. 담당과 트리거를 한 줄로 적는다 — 이 문장이 스킬 발동을 정한다."
model: {model}
tools: env_read, metrics_read, ticket_create
---

## 핵심 역할
{name} 의 담당 범위를 적는다. 무엇을 하는지보다 **무엇을 하지 않는지**를 먼저 적으면
다른 근무자와 겹치지 않는다.

## 작업 원칙
- 단일 측정값으로 판단하지 않는다. 추세와 교차 검증을 함께 낸다.
- 상태를 바꾸는 제안에는 되돌리기 경로를 함께 낸다.

## 입출력 프로토콜
- 입력:
- 출력: 티켓(원인추정·영향범위·근거·조치안·되돌리기경로)

## 에러 핸들링
- 두 번 실패하면 혼자 더 시도하지 않고 escalate 한다.

## 협업 정의
- 누구와 무엇을 합의하는가.
"""


@app.post("/api/worker")
def add_worker(key: str = "", w: dict = Body(...)):
    """근무자 추가. roster 항목과 페르소나 파일을 함께 만든다.

    페르소나 없이 roster 에만 넣으면 렌더가 실패한다. 둘을 한 번에 만드는 이유다.
    """
    _auth(key)
    wid = (w.get("id") or "").strip()
    if not ID_RE.match(wid):
        raise HTTPException(400, "id 는 소문자·숫자·하이픈으로 2~41자여야 한다")
    roster = _load_rt("roster")
    if any(x["id"] == wid for x in roster.get("workers", [])):
        raise HTTPException(400, f"이미 있는 근무자다: {wid}")

    entry = {
        "id": wid,
        "name": w.get("name") or wid,
        "floor": w.get("floor") or "4F",
        "zone": w.get("zone") or "mgmt",
        "runtime": w.get("runtime") or "bastion",
        "autonomy": w.get("autonomy") or "L1",
        "team": w.get("team") or "",
        "model": w.get("model") or "local-small",
        "loops": w.get("loops") or [],
        "assets": w.get("assets") or [],
        "curriculum": w.get("curriculum") or [],
    }
    roster.setdefault("workers", []).append(entry)

    teams = _load_rt("teams")
    if entry["team"]:
        for t in teams.get("teams", []):
            if t["id"] == entry["team"] and wid not in t.get("members", []):
                t.setdefault("members", []).append(wid)

    persona = AGENTS / "personas" / f"{wid}.md"
    persona_text = PERSONA_TEMPLATE.format(
        name=entry["name"],
        model="reasoning" if "reasoning" in entry["model"] else "small")
    errs = validate_all({"roster": roster, "teams": teams})
    # 페르소나 파일은 아직 없으므로 그 오류만 예외로 둔다
    errs = [e for e in errs if f"personas/{wid}.md" not in e]
    if errs:
        raise HTTPException(400, "조직 정합성 오류:\n" + "\n".join(f"· {e}" for e in errs))

    if not persona.exists():
        _write_text(persona, persona_text)
    _dump_rt("roster", roster)
    _dump_rt("teams", teams)
    return {"ok": True, "id": wid, "errors": validate_all(),
            "note": "페르소나 뼈대를 personas/%s.md 에 만들었다. 4단계에서 채워라" % wid}


@app.delete("/api/worker/{wid}")
def del_worker(wid: str, key: str = "", keep_persona: bool = True):
    """근무자 삭제. 팀 명단에서도 빼고, 페르소나는 기본적으로 남긴다.

    페르소나를 지우면 학생이 되돌릴 수 없다. 삭제는 roster 에서 빼는 것으로 충분하고,
    파일은 남겨 두면 다시 넣을 수 있다.
    """
    _auth(key)
    roster = _load_rt("roster")
    ws = roster.get("workers", [])
    if not any(x["id"] == wid for x in ws):
        raise HTTPException(404, f"없는 근무자다: {wid}")
    # 새 리스트를 만들면 ruamel 이 들고 있던 주석 연결이 끊긴다 — 층 구분 주석이
    # 통째로 사라진다. 제자리에서 지워야 앞뒤 주석이 남는다.
    del ws[next(i for i, x in enumerate(ws) if x["id"] == wid)]

    teams = _load_rt("teams")
    for t in teams.get("teams", []):
        if wid in t.get("members", []):
            t["members"].remove(wid)          # 제자리 — 위와 같은 이유

    harness = _load_rt("harness")
    harness.get("workers", {}).pop(wid, None)

    errs = validate_all({"roster": roster, "teams": teams})
    if errs:
        raise HTTPException(400, "삭제하면 조직이 깨진다:\n" + "\n".join(f"· {e}" for e in errs))

    _dump_rt("roster", roster)
    _dump_rt("teams", teams)
    _dump_rt("harness", harness)
    if not keep_persona:
        (AGENTS / "personas" / f"{wid}.md").unlink(missing_ok=True)
    return {"ok": True, "errors": validate_all()}


@app.patch("/api/worker/{wid}")
def patch_worker(wid: str, key: str = "", patch: dict = Body(...)):
    """런타임·모델·자율성·팀 같은 한 필드만 바꾼다. 화면의 드롭다운이 쓴다."""
    _auth(key)
    roster = _load_rt("roster")
    w = next((x for x in roster.get("workers", []) if x["id"] == wid), None)
    if not w:
        raise HTTPException(404, f"없는 근무자다: {wid}")
    allowed = {"name", "runtime", "model", "autonomy", "team", "floor", "zone", "loops", "assets"}
    bad = set(patch) - allowed
    if bad:
        raise HTTPException(400, f"바꿀 수 없는 필드다: {', '.join(sorted(bad))}")

    teams = _load_rt("teams")
    if "team" in patch and patch["team"] != w.get("team"):
        for t in teams.get("teams", []):
            if wid in t.get("members", []):
                t["members"].remove(wid)      # 제자리 — 주석 연결 보존
            if t["id"] == patch["team"]:
                t.setdefault("members", []).append(wid)
    w.update(patch)

    errs = validate_all({"roster": roster, "teams": teams})
    if errs:
        raise HTTPException(400, "조직 정합성 오류:\n" + "\n".join(f"· {e}" for e in errs))
    _dump_rt("roster", roster)
    _dump_rt("teams", teams)
    return {"ok": True, "worker": dict(w), "errors": validate_all()}


# ── API: 적용 ───────────────────────────────────────────────────────
@app.post("/api/render")
def render(key: str = "", worker: str = ""):
    """agentctl 로 런타임 형식으로 렌더한다. 여기까지 와야 실제로 적용된 것이다."""
    _auth(key)
    cmd = ["python3", str(AGENTS / "agentctl"), "render"]
    cmd += [worker] if worker else ["--all"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(AGENTS))
    except (subprocess.TimeoutExpired, OSError) as e:
        raise HTTPException(500, f"렌더 실행 실패: {e}") from e
    return {"ok": r.returncode == 0, "stdout": r.stdout[-8000:], "stderr": r.stderr[-4000:]}


@app.get("/api/backups")
def backups():
    if not BAK.exists():
        return {"backups": []}
    out = [{"file": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime}
           for p in sorted(BAK.iterdir(), reverse=True)[:60]]
    return {"backups": out}


@app.post("/api/restore")
def restore(key: str = "", name: str = ""):
    """백업 되돌리기. 되돌리기 경로 없는 조작을 학생에게 시키지 않기 위한 것이다."""
    _auth(key)
    src = BAK / name
    if not src.exists() or src.parent != BAK:
        raise HTTPException(404, "없는 백업이다")
    target_name = name.rsplit(".", 1)[0]
    dest = None
    for v in FILES.values():
        if v == target_name:
            dest = AGENTS / v
    if dest is None:
        if target_name.endswith(".md"):
            dest = AGENTS / "personas" / target_name
        elif target_name == "experience.json":
            dest = AGENTS / "graph" / target_name
        elif target_name.endswith(".yaml"):
            dest = AGENTS / "loops" / target_name
    if dest is None:
        raise HTTPException(400, "복원 위치를 알 수 없다")
    _write_text(dest, src.read_text(encoding="utf-8"))
    return {"ok": True, "restored": str(dest.name), "errors": validate_all()}


@app.get("/health")
def health():
    errs = validate_all()
    r = _read_yaml("roster")
    return {"ok": not errs, "workers": len(r.get("workers", [])),
            "teams": len(_read_yaml("teams").get("teams", [])),
            "errors": errs}


@app.get("/", response_class=HTMLResponse)
def console(request: Request):
    return tpl.TemplateResponse("agentops.html", {"request": request})
