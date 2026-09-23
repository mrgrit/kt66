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
from functools import wraps
import json
import os
import re
import shutil
from pathlib import Path

import yaml
import configuration
from ruamel.yaml import YAML
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).parent
AGENTS = Path(os.environ.get("AGENTS_DIR", "/agents"))
BAK = AGENTS / ".bak"
API_KEY = os.environ["API_KEY"]

# 토큰당 과금되는 공개 LLM API. 여기 걸리면 조직 저장이 거부된다(validate_all).
# 목록이지 정규식이 아니다 — 랩 안(10.20.x)의 자체 호스팅 OpenAI 호환 엔드포인트는
# 통과해야 하고, 실제로 막고 싶은 것은 "카드가 긁히는 주소" 뿐이다.
METERED_HOSTS = ("api.anthropic.com", "api.openai.com", "api.mistral.ai",
                 "generativelanguage.googleapis.com", "api.cohere.ai",
                 "api.groq.com", "openrouter.ai")

app = FastAPI(title="kt66 agentops", docs_url="/api/docs")
UI_DIR = Path(__file__).resolve().parent / "ui"
if not UI_DIR.is_dir():
    UI_DIR = Path(__file__).resolve().parent.parent / "ui"
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")
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


def _loop_details():
    out = []
    for lid in _loops():
        try:
            value = yaml.safe_load((AGENTS / "loops" / f"{lid}.yaml").read_text())
            out.append({key: value.get(key) for key in ("id", "owner", "cadence")})
        except (ValueError, AttributeError, yaml.YAMLError):
            out.append({"id": lid, "owner": "", "cadence": "원문 확인 필요"})
    return out


# ── 검증 ────────────────────────────────────────────────────────────
def _validate_all(over: dict | None = None) -> list[str]:
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

    # ── 과금 경로 차단 ────────────────────────────────────────────────
    # kt66 은 랩 안의 GPU 와 **이미 구독 중인** Claude Code 세션만 쓴다. 토큰당 과금되는
    # API 를 카탈로그에 되돌려 놓으면 학생이 드롭다운 하나로 청구서를 만들 수 있다 —
    # 그것도 자기 것이 아닌 청구서를. 규칙을 주석으로 적어 두면 다음 편집에서 지워진다.
    # 검사로 둔다: 이 화면과 셸 편집(POST /api/file/roster) 이 같은 문을 지난다.
    endpoints = roster.get("endpoints") or {}
    for ep_name, ep in endpoints.items():
        url = str((ep or {}).get("base_url") or "")
        hit = next((h for h in METERED_HOSTS if h in url), "")
        if hit:
            err.append(f"엔드포인트 {ep_name} 가 토큰당 과금 API 를 가리킨다({hit}) — "
                       f"kt66 은 랩 GPU 와 구독 Claude Code 세션만 쓴다")
    # 모델이 없는 엔드포인트를 가리키면 위 검사를 우회할 길이 생긴다. 같이 막는다.
    for mk, m in (roster.get("models") or {}).items():
        ep_name = (m or {}).get("endpoint")
        if ep_name and ep_name not in endpoints:
            err.append(f"모델 {mk} 의 엔드포인트가 없다: {ep_name}")

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
    import authorization, harness_tools
    err.extend(authorization.validate(g('harness'), workers, [t[0] for t in harness_tools.TOOLS]))
    return err


def validate_all(over: dict | None = None) -> list[str]:
    try:
        errors = _validate_all(over)
        if not over:
            errors.extend(configuration.issues(AGENTS))
        return list(dict.fromkeys(errors))
    except (ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError) as exc:
        return ["설정 구조 오류: " + str(exc)]


def configuration_edit(fn):
    @wraps(fn)
    def locked(*args, **kwargs):
        if kwargs.pop("_already_locked", False):
            return fn(*args, **kwargs)
        with configuration.edit_lock(AGENTS):
            try:
                return fn(*args, **kwargs)
            except (ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError) as exc:
                raise HTTPException(400, str(exc)) from exc
    return locked


def _preflight(updates):
    configuration.preflight(AGENTS, updates)


def _rt_text(data):
    import io
    buf = io.StringIO()
    _rt.dump(data, buf)
    return buf.getvalue()


# ── 쓰기 ────────────────────────────────────────────────────────────
def _backup(p: Path) -> None:
    if not p.exists():
        return
    BAK.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    name = "__".join(p.relative_to(AGENTS).parts)
    shutil.copy2(p, BAK / f"{name}.{stamp}")
    # 무한히 쌓이면 디스크를 먹는다. 파일당 최근 20개만 남긴다.
    keep = sorted(BAK.glob(f"{name}.*"))[:-20]
    for old in keep:
        old.unlink(missing_ok=True)


def _write_text(p: Path, text: str) -> None:
    _backup(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)                     # 원자적 교체 — 반쯤 쓰인 파일을 남기지 않는다


def _auth(key: str | None) -> None:
    if not API_KEY or key != API_KEY:
        raise HTTPException(401, "API 키가 필요하다 — 화면 우측 상단에 서버 .env 의 API_KEY 값을 넣는다"
                            ". LLM API 키가 아니다.")


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
        "loop_details": _loop_details(),
        "skill_catalog": configuration.library(AGENTS),
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
    p = configuration.safe_path(AGENTS, str(p.relative_to(AGENTS)))
    return p.read_text(encoding="utf-8")


# ── API: 저장 ───────────────────────────────────────────────────────
@app.post("/api/file/{name}")
@configuration_edit
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
        _preflight({FILES[name]: text})
        _write_text(AGENTS / FILES[name], text)
    elif name.startswith("persona:"):
        pid = name[8:]
        if not ID_RE.match(pid):
            raise HTTPException(400, "페르소나 id 형식이 잘못됐다")
        configuration.split_markdown(text, require_description=False)
        _preflight({f"personas/{pid}.md": text})
        _write_text(configuration.safe_path(AGENTS, f"personas/{pid}.md"), text)
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
        _preflight({f"loops/{lid}.yaml": text})
        _write_text(configuration.safe_path(AGENTS, f"loops/{lid}.yaml"), text)
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
description: {description}
skills: []
---

## 역할과 책임
{name}의 담당 업무, 하지 않는 일, 완료 조건을 적으세요.

## 업무 선택
연결한 스킬 중 이번 요청에 필요한 절차만 읽으세요.

## 협업과 보고
- 다른 직무의 업무는 해당 담당자에게 필요한 대상·기간·근거를 정리해 안내하세요.
- 관찰 사실, 판단, 미확인 사항, 후속 작업을 구분하세요.
- 실제 모델·권한은 명단과 서버 정책이 결정합니다. 지침으로 권한을 넓히지 마세요.
"""



@app.post("/api/worker")
@configuration_edit
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
        "security_role": w.get("security_role") or "",
        "floor": w.get("floor") or "4F",
        "zone": w.get("zone") or "mgmt",
        "runtime": w.get("runtime") or roster.get("defaults", {}).get("runtime", "claude"),
        "autonomy": w.get("autonomy") or "L1",
        "team": w.get("team") or "",
        "model": w.get("model") or roster.get("defaults", {}).get("model", "cc-haiku"),
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
    persona_text = PERSONA_TEMPLATE.format(name=entry["name"],
        description=json.dumps(entry["name"] + "의 역할·업무 선택·보고 기준", ensure_ascii=False))
    errs = validate_all({"roster": roster, "teams": teams})
    # 페르소나 파일은 아직 없으므로 그 오류만 예외로 둔다
    errs = [e for e in errs if f"personas/{wid}.md" not in e]
    if errs:
        raise HTTPException(400, "조직 정합성 오류:\n" + "\n".join(f"· {e}" for e in errs))

    updates = {"roster.yaml": _rt_text(roster), "teams.yaml": _rt_text(teams)}
    if not persona.exists():
        updates[f"personas/{wid}.md"] = persona_text
    _preflight(updates)
    if not persona.exists():
        _write_text(persona, persona_text)
    _dump_rt("roster", roster)
    _dump_rt("teams", teams)
    return {"ok": True, "id": wid, "errors": validate_all(),
            "note": "페르소나 뼈대를 personas/%s.md 에 만들었다. 4단계에서 채워라" % wid}


@app.delete("/api/worker/{wid}")
@configuration_edit
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

    errs = validate_all({"roster": roster, "teams": teams, "harness": harness})
    if errs:
        raise HTTPException(400, "삭제하면 조직이 깨진다:\n" + "\n".join(f"· {e}" for e in errs))

    updates = {"roster.yaml": _rt_text(roster), "teams.yaml": _rt_text(teams), "harness.yaml": _rt_text(harness)}
    if not keep_persona:
        updates[f"personas/{wid}.md"] = None
    _preflight(updates)
    _dump_rt("roster", roster)
    _dump_rt("teams", teams)
    _dump_rt("harness", harness)
    if not keep_persona:
        path = configuration.safe_path(AGENTS, f"personas/{wid}.md")
        _backup(path)
        path.unlink(missing_ok=True)
    return {"ok": True, "errors": validate_all()}


@app.patch("/api/worker/{wid}")
@configuration_edit
def patch_worker(wid: str, key: str = "", patch: dict = Body(...)):
    """런타임·모델·자율성·팀 같은 한 필드만 바꾼다. 화면의 드롭다운이 쓴다."""
    _auth(key)
    roster = _load_rt("roster")
    w = next((x for x in roster.get("workers", []) if x["id"] == wid), None)
    if not w:
        raise HTTPException(404, f"없는 근무자다: {wid}")
    allowed = {"name", "runtime", "model", "autonomy", "team", "floor", "zone", "loops", "assets", "security_role"}
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
    _preflight({"roster.yaml": _rt_text(roster), "teams.yaml": _rt_text(teams)})
    _dump_rt("roster", roster)
    _dump_rt("teams", teams)
    return {"ok": True, "worker": dict(w), "errors": validate_all()}


# ── API: 적용 ───────────────────────────────────────────────────────
@app.post("/api/render")
@configuration_edit
def render(key: str = "", worker: str = ""):
    """원본을 검증하고 전체 생성 포인터와 활성 버전을 함께 갱신한다."""
    _auth(key)
    if worker and worker not in {w["id"] for w in _read_yaml("roster")["workers"]}:
        raise HTTPException(404, "없는 근무자입니다")
    _preflight({})
    import harness_compiler
    manifests = harness_compiler.compile_all(AGENTS)
    return {"ok": True, "stdout": json.dumps(manifests, ensure_ascii=False, indent=2), "stderr": ""}


@app.get("/api/backups")
def backups():
    if not BAK.exists():
        return {"backups": []}
    out = [{"file": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime}
           for p in sorted(BAK.iterdir(), reverse=True)[:60]]
    return {"backups": out}


@app.post("/api/restore")
@configuration_edit
def restore(key: str = "", name: str = ""):
    """백업 되돌리기. 되돌리기 경로 없는 조작을 학생에게 시키지 않기 위한 것이다."""
    _auth(key)
    src = configuration.safe_path(BAK, name)
    if not src.exists() or src.parent != BAK:
        raise HTTPException(404, "없는 백업이다")
    relative = name.rsplit(".", 1)[0].replace("__", "/")
    dest = configuration.safe_path(AGENTS, relative)
    content = src.read_text(encoding="utf-8")
    key_name = next((k for k, v in FILES.items() if v == relative), None)
    if key_name:
        result = put_file(key_name, key, {"text": content}, _already_locked=True)
    elif relative.startswith("personas/") and dest.suffix == ".md":
        result = put_file("persona:" + dest.stem, key, {"text": content}, _already_locked=True)
    elif relative.startswith("loops/") and dest.suffix == ".yaml":
        result = put_file("loop:" + dest.stem, key, {"text": content}, _already_locked=True)
    elif relative == "graph/experience.json":
        result = put_file("graph", key, {"text": content}, _already_locked=True)
    elif relative.startswith("native/") and dest.suffix == ".md":
        allowed = {"native/AGENTS.md", "native/README.md", "native/.claude/agents/kt66-request-worker.md"}
        if re.fullmatch(r"native/\.agents/skills/[a-z][a-z0-9-]{0,63}/SKILL\.md", relative):
            configuration.validate_skill(dest.parent.name, content)
        elif relative not in allowed:
            raise HTTPException(400, "복원할 지침 경로를 확인하세요")
        elif relative.endswith("kt66-request-worker.md"):
            metadata, _ = configuration.split_markdown(content)
            if metadata.get("name") != "kt66-request-worker":
                raise ValueError("공통 업무 역할의 name을 유지하세요")
        if not content.strip():
            raise ValueError("지침 본문을 입력하세요")
        _preflight({relative: content})
        _write_text(dest, content)
        result = {"ok": True, "errors": validate_all()}
    else:
        raise HTTPException(400, "복원할 원본 경로를 확인하세요")
    return {**result, "restored": relative}



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


@app.middleware("http")
async def activate_saved_harness(request, call_next):
    response = await call_next(request)
    if (request.method in ("POST", "PATCH", "DELETE") and response.status_code < 300
            and (request.url.path.startswith("/api/file/") or request.url.path.startswith("/api/worker")
                 or request.url.path == "/api/restore")):
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import JSONResponse
        import sys
        if str(AGENTS) not in sys.path:
            sys.path.insert(0, str(AGENTS))
        import harness_compiler
        def activate():
            with configuration.edit_lock(AGENTS):
                return harness_compiler.compile_all(AGENTS)
        try:
            await run_in_threadpool(activate)
            response.headers["X-KT66-Harness-Activation"] = "current"
        except Exception as exc:
            return JSONResponse(status_code=409, content={"saved": True, "activated": False,
                "detail": "Configuration saved; harness activation failed: " + str(exc)})
    return response

@app.get("/api/activation")
def active_harness_versions():
    path = AGENTS / "runtimes" / "activation.json"
    return json.loads(path.read_text()) if path.exists() else {"workers": {}}


@app.get("/api/loop-status")
def loop_status():
    path = AGENTS / "tickets" / "loop-engine-status.json"
    if not path.exists():
        return {"status": "not_started"}
    result = json.loads(path.read_text())
    result["heartbeat_recent"] = __import__("time").time() - result.get("at", 0) < 60
    return result

# 사용자 업무는 기존 조직 파일 편집과 같은 백업 경로를 사용한다.
from requests_api import install as install_requests
install_requests(app, AGENTS, API_KEY, tpl, _write_text)

from configuration_api import install as install_configuration
install_configuration(app, AGENTS, API_KEY, _write_text, _backup)

from control_centers_api import install as install_control_centers
install_control_centers(app, AGENTS, API_KEY, tpl, _write_text)

from monitoring_api import install as install_monitoring
install_monitoring(app, AGENTS, API_KEY, tpl)
