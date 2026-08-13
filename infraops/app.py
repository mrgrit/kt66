"""kt66 인프라 요구사항 — 받고, 만들고, **랩에서 확인한다.**

요구사항은 사업 언어로 온다. "추론이 느리니 3층에 한 대 더 넣어 주세요." 어디에
어떻게 놓을지는 적혀 있지 않고, 그 결정이 존 경계·전력·대장에 걸린다는 것도
적혀 있지 않다. 적혀 있지 않은 것을 챙기는 것이 이 실습이다.

판정은 학생의 보고가 아니라 **랩의 실제 상태**로 한다(checks.py). 그래서 이 서비스는
할 일이 적다 — 요구서를 읽어 주고, 검사를 돌리고, 무엇이 왜 안 됐는지 보여 준다.
"""
from __future__ import annotations

import os
import pathlib

import checks
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

HERE = pathlib.Path(__file__).parent
REQ_DIR = pathlib.Path(os.getenv("REQ_DIR", str(HERE / "requirements")))

app = FastAPI(title="kt66 인프라 요구사항", version="1.0")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def load_all() -> tuple[dict[str, dict], list[str]]:
    out, errs = {}, []
    for p in sorted(REQ_DIR.glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for k in ("id", "title", "request", "acceptance", "ground_truth"):
                if k not in d:
                    raise ValueError(f"필수 항목 누락 — {k}")
            if not d["acceptance"]:
                raise ValueError("acceptance 가 비었다 — 확인할 수 없는 요구는 싣지 않는다")
            # 검사의 `id` 는 검사 이름이고, 자산을 가리킬 때는 `asset_id` 를 쓴다.
            # 한 키에 두 뜻을 담으면 보고서에서 무엇이 무엇인지 알 수 없게 된다.
            for a in d["acceptance"]:
                if a.get("type") == "asset_in_ledger" and not a.get("asset_id"):
                    raise ValueError(f"{a.get('id')} — asset_in_ledger 는 asset_id 가 필요하다")
            d["max_weight"] = sum(float(a.get("weight", 0)) for a in d["acceptance"])
            out[d["id"]] = d
        except Exception as e:
            errs.append(f"{p.name}: {e}")
    return out, errs


@app.get("/health")
def health():
    cat, errs = load_all()
    return {"ok": not errs, "requirements": len(cat), "errors": errs}


@app.get("/api/catalog")
def catalog():
    cat, errs = load_all()
    return {"errors": errs, "requirements": [
        {k: v for k, v in d.items() if k != "acceptance"}
        | {"checks": len(d["acceptance"])} for d in cat.values()]}


@app.get("/api/requirement/{rid}")
def one(rid: str):
    cat, _ = load_all()
    if rid not in cat:
        raise HTTPException(404, f"그런 요구사항이 없다: {rid}")
    return cat[rid]


@app.post("/api/verify/{rid}")
async def verify(rid: str):
    """지금 이 순간의 랩 상태로 판정한다. 저장하지 않는다 — 상태는 계속 변한다."""
    cat, _ = load_all()
    if rid not in cat:
        raise HTTPException(404, f"그런 요구사항이 없다: {rid}")
    d = cat[rid]
    items, got = [], 0.0
    for a in d["acceptance"]:
        r = await checks.run(dict(a))
        w = float(a.get("weight", 0))
        if r["passed"]:
            got += w
        items.append({"id": a.get("id"), "type": a.get("type"), "weight": w,
                      "note": a.get("note", ""), **r})
    return {"id": rid, "points": round(got, 1), "max": d["max_weight"],
            "done": got >= d["max_weight"], "items": items}


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "templates" / "infraops.html").read_text(encoding="utf-8")
