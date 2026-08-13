"""서빙 — 활성 버전의 manifest 대로 실제로 답을 만든다.

Ollama 가 붙어 있으면 진짜 모델을 부른다. 없으면 **결정적 모의 응답**으로 떨어진다.
모의라도 manifest 의 효과는 그대로 재현한다:

  · context_tokens 가 작으면 프롬프트가 **실제로 잘린다** → 뒤쪽 내용을 못 본다
  · retrieval 이 꺼져 있으면 사내 지식이 안 붙는다 → 근거 없는 답
  · refuse_patterns 에 걸리면 거부한다 → 정상 질문까지 막히는 것도 그대로
  · temperature 가 높으면 같은 질문에 다른 답이 나온다
  · max_tokens 가 크면 느리다

모의 모드가 "아무거나 돌려주는 가짜"면 실습이 성립하지 않는다. 학생이 설정을
고쳤을 때 **지표가 그 방향으로 움직여야** 인과를 배운다. 그래서 모의 쪽에도
같은 규칙을 넣었다. GPU 가 없는 교실에서도 수업이 되게 하려는 것이다.
"""
from __future__ import annotations

import hashlib
import os
import random
import re
import time

import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL", "").rstrip("/")
# 토큰 추정. 한국어는 글자당 토큰이 크다 — 대략 1.6자/토큰으로 잡는다.
# 정확한 값이 필요한 자리가 아니다. 필요한 것은 "길면 잘린다"는 성질뿐이다.
CHARS_PER_TOKEN = 1.6


def est_tokens(s: str) -> int:
    return int(len(s) / CHARS_PER_TOKEN) + 1


def build_prompt(m: dict, user: str) -> tuple[str, dict]:
    """manifest 대로 최종 프롬프트를 조립한다. 무엇이 잘렸는지도 함께 돌려준다."""
    info = {"truncated": False, "retrieved_chars": 0, "dropped_chars": 0,
            "retrieval_empty": False}
    parts = [m.get("system_prompt", "")]
    if m.get("retrieval"):
        k = (m.get("knowledge") or "")[: int(m.get("retrieval_chars", 1200))]
        if k:
            parts.append("### 사내 지식\n" + k)
            info["retrieved_chars"] = len(k)
        else:
            # 켜져 있는데 붙일 것이 없다. 새 버전을 만들면서 knowledge.md 를 안
            # 가져온 경우다 — 증상은 'retrieval 을 껐을 때와 똑같다'. 구분해 주지
            # 않으면 학생이 이미 켜 놓은 스위치를 계속 켠다.
            info["retrieval_empty"] = True
    head = "\n\n".join(p for p in parts if p)

    budget = int(m.get("context_tokens", 2048)) - est_tokens(head) - 64
    if budget < 64:
        budget = 64
    max_user_chars = int(budget * CHARS_PER_TOKEN)
    if len(user) > max_user_chars:
        info["truncated"] = True
        info["dropped_chars"] = len(user) - max_user_chars
        user = user[:max_user_chars]
    return head + "\n\n### 질문\n" + user, info


def refused(m: dict, text: str) -> str | None:
    for pat in m.get("refuse_patterns") or []:
        try:
            if re.search(pat, text, re.I):
                return pat
        except re.error:
            continue
    return None


async def generate(m: dict, user: str) -> dict:
    t0 = time.time()
    hit = refused(m, user)
    if hit:
        return {"text": "죄송합니다. 이 요청에는 답변드릴 수 없습니다.",
                "refused": True, "refuse_pattern": hit, "truncated": False,
                "retrieved_chars": 0, "latency_ms": int((time.time() - t0) * 1000),
                "backend": "guardrail", "error": None}

    prompt, info = build_prompt(m, user)
    if OLLAMA_URL:
        try:
            async with httpx.AsyncClient(timeout=60.0) as c:
                r = await c.post(f"{OLLAMA_URL}/api/generate", json={
                    "model": m.get("base_model"), "prompt": prompt, "stream": False,
                    "options": {"temperature": float(m.get("temperature", 0.7)),
                                "top_p": float(m.get("top_p", 0.9)),
                                "num_predict": int(m.get("max_tokens", 512))}})
                if r.status_code == 200:
                    return {"text": r.json().get("response", ""), "refused": False,
                            "refuse_pattern": None, "backend": "ollama", "error": None,
                            "latency_ms": int((time.time() - t0) * 1000), **info}
                err = f"ollama {r.status_code}"
        except Exception as e:
            err = f"ollama 도달 실패: {type(e).__name__}"
    else:
        err = None

    # ── 모의 백엔드 ────────────────────────────────────────────────
    # 같은 (버전·질문)이면 같은 답이 나오게 해시로 씨앗을 잡는다. 단 temperature 가
    # 높으면 일부러 흔든다 — "답이 매번 다르다"는 티켓이 재현되어야 하기 때문이다.
    seed = int(hashlib.sha256((m.get("version", "") + user).encode()).hexdigest()[:8], 16)
    t = float(m.get("temperature", 0.7))
    rng = random.Random(seed + (int(time.time() * 10) if t > 0.8 else 0))
    body = []
    if info["retrieved_chars"]:
        body.append("사내 자료를 근거로 답합니다.")
    else:
        body.append("일반적인 기준으로 답합니다.")
    if info["truncated"]:
        body.append(f"(입력이 길어 뒤쪽 {info['dropped_chars']}자를 보지 못했습니다.)")
    body.append(rng.choice(["확인해 보시기 바랍니다.", "담당 부서에 문의하십시오.",
                            "아래 절차를 따르십시오."]))
    # max_tokens 가 크면 실제로 오래 걸리게 만든다 — 지표가 설정을 따라가야 한다.
    await _sleep(min(2.0, int(m.get("max_tokens", 512)) / 900.0))
    return {"text": " ".join(body), "refused": False, "refuse_pattern": None,
            "backend": "mock", "error": err,
            "latency_ms": int((time.time() - t0) * 1000), **info}


async def _sleep(sec: float):
    import asyncio
    await asyncio.sleep(sec)
