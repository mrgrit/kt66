#!/usr/bin/env python3
"""시나리오 선언 검증.

이 검증기가 있는 이유는 하나다. `envsim/assets.yaml` 은 자기가 네 곳의 단일 진실원천이라고
주장한다 — 시뮬레이터 · 관제 화면 · CMDB · **시나리오의 주입 대상 이름공간**. 마지막 것은
아무도 강제하지 않으면 금세 거짓말이 된다. 시나리오에 `dgx-01` 이라고 적어 두고 실제 자산은
`dgx-spark-01` 인 상태로 학기가 시작되면, 그 주차 수업이 통째로 날아간다.

그래서 여기서 대조한다:
  · target.asset 이 자산 대장(it_assets/facility/racks)에 실재하는가
  · target.floor 가 실재하는 층인가
  · envsim_fault 의 fault 이름이 시뮬레이터가 아는 10종 안에 있는가
  · ID 중복, 주차 범위, visibility/status 어휘
  · 카탈로그의 주차별 배치 요약과 실제 week 값이 맞는가

    사용:  python3 scenarios/validate.py
"""
from __future__ import annotations

import pathlib
import sys

import yaml

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
ASSETS = ROOT / "envsim" / "assets.yaml"

# envsim/model.py 의 FAULTS 와 같아야 한다. 시뮬레이터가 진짜다.
FAULTS = {"utility_fail", "generator_fail", "chiller_fail", "crac_fail", "pdu_overload",
          "smoke", "door_forced", "door_held", "cctv_offline", "humidity_drift"}
METHODS = {"envsim_fault", "envsim_shed", "docker_control", "container_exec", "traffic_gen",
           "gpu_workload", "config_drift", "log_inject", "agent_loop", "manual"}
STATUS = {"ready", "partial", "planned"}
CATEGORIES = {"inc", "chg", "sec", "flt", "gpu", "env", "dr", "aud", "agt"}

# docs/시나리오_카탈로그.md 의 '주차별 배치 요약'. 여기서 벗어나면 커리큘럼이 어긋난 것이다.
EXPECTED_PER_WEEK = {4: 2, 5: 2, 6: 3, 8: 3, 9: 3, 10: 4, 11: 6, 12: 5, 13: 5, 14: 1}


def load_assets() -> tuple[set[str], set[str]]:
    """자산 대장에서 (주입 가능한 이름 전부, 층 id) 를 뽑는다."""
    d = yaml.safe_load(ASSETS.read_text(encoding="utf-8"))
    names = {a["id"] for a in d["it_assets"]}
    names |= {r["id"] for r in d.get("racks", [])}
    f = d["facility"]
    if f.get("utility"):
        names.add(f["utility"]["id"])
    for key in ("generator", "ups", "pdu", "chiller", "crac", "fire", "security"):
        names |= {i["id"] for i in f.get(key, [])}
    floors = {x["id"] for x in d["floors"]}
    # smoke 는 층 단위로 주입한다. humidity_drift 는 아일 단위다.
    names |= floors | {r["aisle"] for r in d.get("racks", [])}
    return names, floors


def main() -> int:
    names, floors = load_assets()
    files = sorted(p for p in HERE.glob("*.yaml") if not p.name.startswith("_"))
    errors: list[str] = []
    warns: list[str] = []
    seen: set[str] = set()
    rows = []

    for p in files:
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"{p.name}: YAML 파싱 실패 — {e}")
            continue

        def err(m):
            errors.append(f"{p.name}: {m}")

        sid = d.get("id")
        if not sid:
            err("id 없음")
        elif sid in seen:
            err(f"id 중복 — {sid}")
        else:
            seen.add(sid)
        if sid and p.stem != sid:
            err(f"파일명({p.stem})과 id({sid}) 불일치")

        if d.get("category") not in CATEGORIES:
            err(f"category 어휘 밖 — {d.get('category')}")
        if not isinstance(d.get("week"), int) or not 1 <= d["week"] <= 15:
            err(f"week 범위 밖 — {d.get('week')}")
        if d.get("visibility") not in {"public", "private"}:
            err(f"visibility 어휘 밖 — {d.get('visibility')}")
        if not isinstance(d.get("difficulty"), int) or not 1 <= d["difficulty"] <= 5:
            err(f"difficulty 범위 밖 — {d.get('difficulty')}")

        inj = d.get("inject") or {}
        if inj.get("method") not in METHODS:
            err(f"inject.method 어휘 밖 — {inj.get('method')}")

        impl = d.get("impl") or {}
        status = impl.get("status")
        if status not in STATUS:
            err(f"impl.status 어휘 밖 — {status}")
        if status in {"partial", "planned"} and not impl.get("note"):
            err(f"impl.status={status} 인데 note 가 없다 — 무엇이 없는지 적어야 한다")

        # 주입 대상이 실재하는가. 이 검증이 이 파일의 존재 이유다.
        tgt = inj.get("target") or {}
        asset, floor = tgt.get("asset"), tgt.get("floor")
        if floor and floor not in floors:
            err(f"target.floor 가 대장에 없다 — {floor}")
        if asset and asset not in names:
            err(f"target.asset 이 대장에 없다 — {asset}")
        if status == "ready" and not asset and inj.get("method") != "manual":
            warns.append(f"{p.name}: ready 인데 target.asset 이 비어 있다")

        # envsim_fault 의 fault 이름은 시뮬레이터가 아는 것이어야 한다
        params = inj.get("params") or {}
        seq = params.get("sequence") or ([params] if params.get("fault") else [])
        for step in seq:
            fname, ftgt = step.get("fault"), step.get("target")
            if inj.get("method") == "envsim_fault" and status == "ready":
                if fname not in FAULTS:
                    err(f"envsim 이 모르는 고장 — {fname}")
                if ftgt and str(ftgt) not in names:
                    err(f"고장 주입 대상이 대장에 없다 — {ftgt}")

        for key in ("symptom", "evidence", "expect", "forbidden"):
            if not d.get(key):
                err(f"{key} 가 비어 있다")
        for e in d.get("expect") or []:
            if not e.get("action") or not isinstance(e.get("weight"), int):
                err(f"expect 항목에 action/weight 누락 — {e}")

        casc = d.get("cascade")
        if isinstance(casc, dict) and "enforced" not in casc:
            err("cascade 에 enforced 가 없다 — 자동 발생 여부를 명시해야 한다")

        rows.append((sid, d.get("week"), d.get("category"), d.get("visibility"),
                     d.get("difficulty"), status,
                     sum(e.get("weight", 0) for e in d.get("expect") or [])))

    # 주차별 배치가 카탈로그와 맞는가
    per_week: dict[int, int] = {}
    for _, wk, *_ in rows:
        per_week[wk] = per_week.get(wk, 0) + 1
    for wk, n in sorted(EXPECTED_PER_WEEK.items()):
        got = per_week.get(wk, 0)
        if got != n:
            warns.append(f"주차 {wk}: 카탈로그는 {n}종인데 {got}종이다")

    # ── 출력 ──────────────────────────────────────────────────────
    print(f"시나리오 {len(rows)}종\n")
    print(f"{'ID':<8}{'주차':>4} {'분류':<5}{'공개':<9}{'난이':>3}  {'주입':<8}배점")
    print("─" * 56)
    for sid, wk, cat, vis, dif, st, w in sorted(rows, key=lambda r: (r[1] or 0, r[0])):
        mark = {"ready": "●", "partial": "◐", "planned": "○"}.get(st, "?")
        print(f"{sid:<8}{wk:>4} {cat:<5}{vis:<9}{dif:>3}  {mark} {st:<7}{w:>3}")

    by_status: dict[str, int] = {}
    for *_, st, _ in rows:
        by_status[st] = by_status.get(st, 0) + 1
    print("\n주입 상태  ● ready {r} · ◐ partial {p} · ○ planned {n}".format(
        r=by_status.get("ready", 0), p=by_status.get("partial", 0),
        n=by_status.get("planned", 0)))

    if warns:
        print("\n[경고]")
        for w in warns:
            print(" ·", w)
    if errors:
        print("\n[오류]")
        for e in errors:
            print(" ✗", e)
        return 1
    print("\n검증 통과 — 모든 주입 대상이 자산 대장에 실재한다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
