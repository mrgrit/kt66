"""설정 원본·스킬·R&R의 공통 검증과 편집. 모델 세션을 실행하지 않는다."""
from contextlib import contextmanager
from pathlib import Path
import datetime
import fcntl
import hashlib
import io
import json
import re
import tempfile

import yaml
from ruamel.yaml import YAML

SKILL_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
WORKER_ID = re.compile(r"[a-z][a-z0-9-]{1,40}")
CORE = ("company.yaml", "departments.yaml", "teams.yaml", "roster.yaml", "harness.yaml")


class Conflict(ValueError):
    pass


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def safe_path(root, relative):
    root = Path(root)
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("허용되지 않은 파일 경로입니다")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("설정 경로 밖의 파일을 사용할 수 없습니다")
    if any(p.is_symlink() for p in (path, *path.parents) if p != root and root in p.parents):
        raise ValueError("설정 편집에는 심볼릭 링크를 사용할 수 없습니다")
    return path


@contextmanager
def edit_lock(root):
    lock = Path(root) / "native/.edit.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def split_markdown(content, require_description=True):
    if not isinstance(content, str) or not content.strip() or len(content) > 30000:
        raise ValueError("지침은 1~30000자입니다")
    if not content.startswith("---\n"):
        raise ValueError("---로 시작하는 YAML 머리말이 필요합니다")
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ValueError("YAML 머리말을 ---로 닫아 주세요")
    metadata = yaml.safe_load(parts[1])
    if not isinstance(metadata, dict):
        raise ValueError("YAML 머리말은 항목으로 작성하세요")
    if (require_description or "description" in metadata) and (not isinstance(metadata.get("description"), str) or not metadata["description"].strip()):
        raise ValueError("description에 역할 또는 스킬 선택 기준을 입력하세요")
    return metadata, parts[2].lstrip("\n")


def skill_path(root, name):
    if not isinstance(name, str) or not SKILL_ID.fullmatch(name):
        raise ValueError("스킬 이름은 소문자로 시작하는 영문·숫자·하이픈 1~64자입니다")
    return safe_path(root, f"native/.agents/skills/{name}/SKILL.md")


def validate_skill(name, content):
    from harness_compiler import skill_metadata
    split_markdown(content)
    return skill_metadata(name, content)


def automatic_skills():
    from request_runtime import SKILLS
    uses = {}
    for capability, name in SKILLS.items():
        uses.setdefault(name, []).append("사용자 업무: " + capability)
    uses["request-coordination"] = ["사용자 업무 계획·최종 검토"]
    uses["system-diagnostics"] = ["디스크 조회 권한이 있는 사용자 업무"]
    return uses


def library(root):
    root = Path(root)
    workers = yaml.safe_load((root / "roster.yaml").read_text())["workers"]
    labels = {w["id"]: w["name"] for w in workers}
    references = {}
    errors = []
    from harness_compiler import persona_skills
    for p in sorted((root / "personas").glob("*.md")):
        try:
            for name in persona_skills(safe_path(root, str(p.relative_to(root))).read_text()):
                references.setdefault(name, []).append({
                    "id": p.stem, "name": labels.get(p.stem, p.stem),
                    "active": p.stem in labels, "path": str(p.relative_to(root))})
        except (ValueError, yaml.YAMLError) as exc:
            errors.append(f"{p.name}: {exc}")
    rows = []
    automatic = automatic_skills()
    for p in sorted((root / "native/.agents/skills").glob("*/SKILL.md")):
        name = p.parent.name
        try:
            content = skill_path(root, name).read_text()
            meta = validate_skill(name, content)
            rows.append(dict(name=name, path=str(p.relative_to(root)), **meta,
                             workers=references.get(name, []), automatic=automatic.get(name, []),
                             deletable=not references.get(name) and name not in automatic))
        except (ValueError, yaml.YAMLError) as exc:
            errors.append(f"스킬 {name}: {exc}")
    available = {row["name"] for row in rows}
    for name in sorted((set(references) | set(automatic)) - available):
        errors.append(f"연결되었거나 실행기에 필요한 스킬이 없습니다: {name}")
    return {"skills": rows, "errors": errors}


def source_documents(root):
    root = Path(root)
    paths = [root / p for p in CORE]
    paths += list((root / "personas").glob("*.md"))
    paths += list((root / "loops").glob("*.yaml"))
    paths += list((root / "native").rglob("*.md"))
    return {str(p.relative_to(root)): safe_path(root, str(p.relative_to(root))).read_text()
            for p in paths if p.is_file()}


@contextmanager
def snapshot(root, updates=None):
    documents = source_documents(root)
    documents.update(updates or {})
    with tempfile.TemporaryDirectory(prefix="kt66-config-check-") as td:
        target = Path(td)
        for relative, content in documents.items():
            if content is None:
                continue
            path = safe_path(target, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        yield target


def issues(root):
    """실행기가 소비하는 연결만 검증한다. 보관된 미연결 루프·역할은 허용한다."""
    root = Path(root)
    errors = []
    roster = yaml.safe_load((root / "roster.yaml").read_text())
    teams = yaml.safe_load((root / "teams.yaml").read_text())["teams"]
    defaults = roster.get("defaults", {})
    workers = roster["workers"]
    ids = [w["id"] for w in workers]
    if len(set(ids)) != len(ids):
        errors.append("근무자 ID가 중복됩니다")
    for worker in workers:
        w = {**defaults, **worker}
        if w.get("runtime") not in ("claude", "codex"):
            errors.append(f"{w['id']}: 현재 실행기는 Claude 또는 Codex만 지원합니다")
        model = roster.get("models", {}).get(w.get("model"), {})
        if model.get("endpoint") != {"claude": "claude-code", "codex": "codex-cli"}.get(w.get("runtime")):
            errors.append(f"{w['id']}: 런타임과 모델의 구독 경로가 맞지 않습니다")
        for field in ("loops", "assets"):
            values = w.get(field, [])
            if (not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values)
                    or len(set(values)) != len(values)):
                errors.append(f"{w['id']}: {field}는 중복 없는 문자열 목록이어야 합니다")
        if w.get("autonomy") == "L3" and not w.get("loops"):
            errors.append(f"{w['id']}: L3에는 등록된 업무 루프가 필요합니다")
        primary = next((t for t in teams if t["id"] == w.get("team")), None)
        if primary is None or w["id"] not in primary.get("members", []):
            errors.append(f"{w['id']}: 주 소속 팀과 팀의 근무자 명단이 일치하지 않습니다")
        path = root / "personas" / f"{w['id']}.md"
        if not path.is_file():
            errors.append(f"{w['id']}: 페르소나가 없습니다")
        else:
            try:
                split_markdown(path.read_text(), require_description=False)
            except (ValueError, yaml.YAMLError) as exc:
                errors.append(f"{w['id']}: {exc}")
    from loop_engine import cron_matches
    for path in sorted((root / "loops").glob("*.yaml")):
        try:
            loop = yaml.safe_load(path.read_text())
            if not isinstance(loop, dict) or loop.get("id") != path.stem:
                raise ValueError("파일명과 루프 id가 일치해야 합니다")
            if not isinstance(loop.get("owner"), str) or not isinstance(loop.get("steps"), list) or not loop["steps"]:
                raise ValueError("owner와 비어 있지 않은 steps 목록이 필요합니다")
            cron_matches(loop.get("cadence", ""), datetime.datetime(2026, 1, 1))
            for w in workers:
                if path.stem in w.get("loops", []) and loop["owner"] != w["id"]:
                    raise ValueError(f"명단의 담당자 {w['id']}와 owner {loop['owner']}가 다릅니다")
        except (ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
            errors.append(f"{path.name}: {exc}")
    errors.extend(library(root)["errors"])
    return errors


def preflight(root, updates):
    """운영 원본을 쓰기 전에 격리된 사본에서 실제 컴파일러로 확인한다."""
    import harness_compiler
    with snapshot(root, updates) as candidate:
        errors = issues(candidate)
        if errors:
            raise ValueError("\n".join(errors))
        harness_compiler.compile_all(candidate)


def revision(root, worker_id):
    path = safe_path(root, f"personas/{worker_id}.md")
    return sha(path.read_text() + "".join((Path(root) / name).read_text() for name in CORE))


def worker_detail(root, worker_id):
    if not WORKER_ID.fullmatch(worker_id):
        raise ValueError("근무자 ID를 확인하세요")
    import harness_compiler
    root = Path(root)
    before = revision(root, worker_id)
    with snapshot(root) as candidate:
        _, manifest = harness_compiler.compile_worker(worker_id, candidate)
    if before != revision(root, worker_id):
        raise ValueError("조회 중 설정이 변경되었습니다. 다시 불러오세요")
    content = (root / "personas" / f"{worker_id}.md").read_text()
    meta, body = split_markdown(content, require_description=False)
    return dict(worker=manifest["worker"], department=manifest["department"],
                team=manifest["team"], authorization=manifest["authorization"],
                policy=manifest["policy"], available_tools=manifest["available_tools"],
                description=meta.get("description", meta.get("name", manifest["worker"]["name"])), instructions=body,
                skills=list(manifest["role_skills"]), sha256=before,
                sources=[f"personas/{worker_id}.md", "roster.yaml", "teams.yaml", "departments.yaml", "harness.yaml"])


def assignment_changes(root, worker_id, body):
    if set(body) - {"sha256", "description", "instructions", "skills", "assets", "loops"}:
        raise ValueError("지원하지 않는 담당 설정 항목입니다")
    current = worker_detail(root, worker_id)
    if body.get("sha256") != current["sha256"]:
        raise Conflict("다른 편집 내용이 있습니다. 다시 불러온 뒤 저장하세요")
    description, instructions = body.get("description"), body.get("instructions")
    if not isinstance(description, str) or not description.strip() or not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("역할 요약과 R&R 본문을 입력하세요")
    for key in ("skills", "assets", "loops"):
        value = body.get(key)
        if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value) or len(set(value)) != len(value):
            raise ValueError(f"{key}는 중복 없는 문자열 목록이어야 합니다")
    for name in body["skills"]:
        path = skill_path(root, name)
        if not path.is_file():
            raise ValueError("연결할 스킬이 없습니다: " + name)
    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    rt.indent(mapping=2, sequence=4, offset=2)
    persona = f"personas/{worker_id}.md"
    original = safe_path(root, persona).read_text()
    meta = rt.load(original.split("---", 2)[1])
    meta["description"] = description
    meta["skills"] = body["skills"]
    stream = io.StringIO()
    rt.dump(meta, stream)
    text = "---\n" + stream.getvalue() + "---\n\n" + instructions.rstrip() + "\n"
    split_markdown(text)
    roster = rt.load((Path(root) / "roster.yaml").read_text())
    worker = next(w for w in roster["workers"] if w["id"] == worker_id)
    worker["assets"], worker["loops"] = body["assets"], body["loops"]
    stream = io.StringIO()
    rt.dump(roster, stream)
    return {persona: text, "roster.yaml": stream.getvalue()}


def audit(root):
    import harness_compiler
    root = Path(root)
    errors = issues(root)
    try:
        hashes = {p: hashlib.sha256(value).hexdigest() for p, value in harness_compiler.read_sources(root).items()}
    except (ValueError, yaml.YAMLError) as exc:
        hashes = {}
        errors.append(str(exc))
    roster = yaml.safe_load((root / "roster.yaml").read_text())
    active_path = root / "runtimes/activation.json"
    active = json.loads(active_path.read_text()).get("workers", {}) if active_path.is_file() else {}
    rows = []
    for w in roster["workers"]:
        # Container and host activation paths differ; resolve the local rendered link.
        defaults = roster.get("defaults", {})
        path = root / "runtimes" / w.get("runtime", defaults.get("runtime", "")) / "rendered" / w["id"] / "manifest.json"
        manifest = json.loads(path.read_text()) if path.is_file() else {}
        implementation = manifest.get("implementation_hashes", {})
        implementation_current = bool(implementation) and all(
            (harness_compiler.ROOT / name).is_file() and
            hashlib.sha256((harness_compiler.ROOT / name).read_bytes()).hexdigest() == value
            for name, value in implementation.items()
            if Path(name).name == name)
        rows.append(dict(id=w["id"], name=w["name"], version=manifest.get("version"),
                         current=bool(hashes) and implementation_current and manifest.get("source_hashes") == hashes
                         and active.get(w["id"], {}).get("version") == manifest.get("version")))
    mapping = [
        ("company.yaml", "회사", "회사 목표·판단 원칙", "조회·원문 편집"),
        ("departments.yaml", "조직", "부서 책임·제외 업무·에스컬레이션", "조회·원문 편집"),
        ("teams.yaml", "팀", "팀 구성·KPI·협업 지표", "조회·원문 편집"),
        ("roster.yaml", "근무자", "주 소속·모델·루프·담당 자산", "담당 설정·원문 편집"),
        ("personas/*.md", "근무자 → R&R·스킬", "역할·업무 경계·협업·스킬 선택", "담당 설정·원문 편집"),
        ("native/.agents/skills/*/SKILL.md", "일하는 방식 → 스킬", "상세 업무 절차", "추가·수정·삭제·근무자 연결"),
        ("loops/*.yaml", "일하는 방식 → 루프", "실행 시점·트리거·절차", "원문 편집·근무자 연결"),
        ("harness.yaml", "일하는 방식", "상속 정책·직무 상한·예산·시간대", "조회·원문 편집"),
        ("native/AGENTS.md · native/.claude/agents/", "업무 요청 → 지침 실습", "사용자 업무 공통 지침", "기존 지침 편집"),
        ("graph/experience.json", "팀 → 경험그래프", "경험 자료", "조회·원문 편집; 하네스 지침과 별도"),
        ("runtimes/ · evidence/ · tickets/", "적용 / 4F 관제", "생성 사본·증거·업무 상태", "조회; 원본 편집 대상 아님"),
    ]
    return {"errors": errors, "workers": rows,
            "files": [dict(path=p, screen=s, purpose=m, support=c) for p, s, m, c in mapping],
            "notes": [
                "persona의 model·tools 같은 설명 필드는 실제 런타임·권한 설정을 대신하지 않습니다.",
                "roster.assets는 담당 설명입니다. 실제 조회 범위는 보안 직무의 자산 정책과 도구가 결정합니다.",
                "컴파일러는 roster.team의 주 소속 팀·부서를 전달합니다. 다른 팀의 members에 참여해도 해당 팀 KPI 전체가 자동 병합되지는 않습니다.",
                "스킬 추가·연결은 도구 권한을 넓히지 않습니다. 상세 본문은 필요한 업무에서 skill_read로 읽습니다.",
                "루프는 owner와 roster.loops가 연결돼야 실행됩니다. YAML의 steps·gates는 모델 지침입니다.",
            ]}
