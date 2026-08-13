"""모델 버전 레지스트리 — 파일이 진실원천이다.

한 버전은 디렉터리 하나다.

    models/v1/manifest.yaml     이 버전이 무엇인가 (베이스 모델·시스템 프롬프트·파라미터·가드레일)
    models/v1/knowledge.md      검색해 붙일 사내 지식 (없으면 없는 대로)

DB 를 쓰지 않는 이유는 agentops 와 같다. 학생이 웹으로 고친 것과 셸에서 고친 것이
같은 파일이어야 하고, `git diff` 로 무엇이 바뀌었는지 보여야 한다. 모델 운영에서
**무엇을 바꿨는지 말할 수 없는 변경**은 사고로 이어진다 — 그 습관을 여기서 들인다.

버전은 지우지 않는다. 되돌릴 수 없는 배포는 배포가 아니다.
"""
from __future__ import annotations

import os
import pathlib
import re

import yaml

ROOT = pathlib.Path(os.getenv("MODELS_DIR", "/models"))
ACTIVE_FILE = ROOT / "ACTIVE"
VER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,30}$")

# manifest 에서 학생이 만질 수 있는 것. 여기 없는 키는 저장 단계에서 거절한다 —
# 아무 키나 받으면 "고쳤는데 아무 일도 안 일어난다"가 되고, 그건 배우는 게 아니라
# 헷갈리는 것이다.
FIELDS = {
    "base_model": str,        # ollama 모델 이름
    "system_prompt": str,     # 시스템 프롬프트
    "temperature": float,
    "top_p": float,
    "max_tokens": int,
    "context_tokens": int,    # 이 길이를 넘는 요청은 잘린다 — 실패의 흔한 원인
    "retrieval": bool,        # knowledge.md 를 붙일 것인가
    "retrieval_chars": int,   # 얼마나 붙일 것인가
    "refuse_patterns": list,  # 이 정규식에 걸리면 거부한다 (가드레일)
    "note": str,              # 왜 이 버전을 만들었는가. 비워 두면 저장은 되지만 배포가 막힌다
}


class RegistryError(ValueError):
    pass


def versions() -> list[str]:
    if not ROOT.is_dir():
        return []
    return sorted(p.name for p in ROOT.iterdir()
                  if p.is_dir() and (p / "manifest.yaml").exists())


def active() -> str:
    if ACTIVE_FILE.exists():
        v = ACTIVE_FILE.read_text(encoding="utf-8").strip()
        if v in versions():
            return v
    vs = versions()
    return vs[0] if vs else ""


def load(v: str) -> dict:
    p = ROOT / v / "manifest.yaml"
    if not p.exists():
        raise RegistryError(f"그런 버전이 없다: {v}")
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    d["version"] = v
    d["knowledge"] = knowledge(v)
    return d


def knowledge(v: str) -> str:
    p = ROOT / v / "knowledge.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def validate(d: dict) -> list[str]:
    """저장 전 검사. 무엇이 왜 틀렸는지 한국어로 돌려준다."""
    errs = []
    unknown = [k for k in d if k not in FIELDS and k not in ("version", "knowledge")]
    if unknown:
        errs.append(f"모르는 항목: {', '.join(unknown)} — manifest 에 없는 키는 서빙에 "
                    f"쓰이지 않는다. 고쳐도 아무 일이 안 일어나면 여기를 의심하라")
    if not str(d.get("base_model", "")).strip():
        errs.append("base_model 이 비었다")
    for k, cast in (("temperature", float), ("top_p", float),
                    ("max_tokens", int), ("context_tokens", int)):
        if k in d:
            try:
                cast(d[k])
            except Exception:
                errs.append(f"{k} 는 {cast.__name__} 여야 한다 (받은 값: {d[k]!r})")
    t = float(d.get("temperature", 0) or 0)
    if not 0.0 <= t <= 2.0:
        errs.append(f"temperature 는 0~2 다 (받은 값: {t})")
    if int(d.get("context_tokens", 0) or 0) < 256:
        errs.append("context_tokens 가 256 미만이다 — 대부분의 질문이 잘려서 "
                    "'모델이 멍청해진 것처럼' 보인다")
    for pat in d.get("refuse_patterns") or []:
        try:
            re.compile(pat)
        except re.error as e:
            errs.append(f"refuse_patterns 의 정규식이 깨졌다: {pat!r} — {e}")
    return errs


def save(v: str, manifest: dict, knowledge_md: str | None = None) -> dict:
    if not VER_RE.match(v):
        raise RegistryError(f"버전 이름은 소문자·숫자·._- 만 쓴다: {v!r}")
    errs = validate(manifest)
    if errs:
        raise RegistryError("\n".join(errs))
    d = ROOT / v
    d.mkdir(parents=True, exist_ok=True)
    body = {k: manifest[k] for k in FIELDS if k in manifest}
    tmp = d / "manifest.yaml.tmp"
    tmp.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    tmp.replace(d / "manifest.yaml")
    if knowledge_md is not None:
        (d / "knowledge.md").write_text(knowledge_md, encoding="utf-8")
    return load(v)


def deploy(v: str) -> str:
    if v not in versions():
        raise RegistryError(f"그런 버전이 없다: {v}")
    m = load(v)
    # 배포 게이트. 왜 바꿨는지 적지 않은 버전은 못 올린다 — 다음 사람이 사고 때
    # 이 변경을 이해할 수 없으면 롤백 판단이 늦어진다.
    if not str(m.get("note", "")).strip():
        raise RegistryError(f"{v} 에 note 가 없다 — 왜 이 버전을 만들었는지 적어야 배포된다")
    ACTIVE_FILE.write_text(v, encoding="utf-8")
    return v
