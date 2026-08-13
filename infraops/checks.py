"""요구사항 판정 — **랩을 직접 들여다본다.**

체크박스를 믿지 않는다. 학생이 "했습니다"라고 누르는 것과 실제로 되어 있는 것은
다르고, 그 차이가 인프라 사고의 대부분이다. 그래서 모든 판정은 다음 셋 중 하나에서
온다.

  docker    컨테이너가 정말 떠 있는가, 어느 망에 어느 주소로 붙었는가
  envsim    자산 대장(assets.yaml)에 등재됐는가, 전력·열 여유가 남는가
  파일      저장소의 파일이 실제로 바뀌었는가 (compose·대장)

판정은 통과/실패만 돌려주지 않는다. **무엇을 봤고 무엇이 없었는지**를 함께 돌려준다.
"안 됨"만 보여 주면 학생은 찍어 맞히기 시작한다.
"""
from __future__ import annotations

import os
import pathlib
import re

import httpx

ENVSIM_URL = os.getenv("ENVSIM_URL", "http://10.20.60.10:8000")
DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")
REPO = pathlib.Path(os.getenv("REPO_DIR", "/repo"))


async def _docker(path: str, params: dict | None = None):
    tr = httpx.AsyncHTTPTransport(uds=DOCKER_SOCK)
    async with httpx.AsyncClient(transport=tr, base_url="http://d", timeout=8.0) as c:
        r = await c.get(path, params=params or {})
        return r.status_code, (r.json() if r.status_code == 200 else None)


async def _envsim(path: str):
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.get(f"{ENVSIM_URL}{path}")
        return r.status_code, (r.json() if r.status_code == 200 else None)


async def container_running(spec: dict) -> tuple[bool, str]:
    name = spec["name"]
    st, data = await _docker("/containers/json", {"all": "true"})
    if st != 200:
        return False, "docker 조회 실패 — 판정할 수 없다"
    for ct in data:
        for n in ct.get("Names") or []:
            if n.lstrip("/") == name:
                run = ct.get("State") == "running"
                return run, f"{name}: {ct.get('State')} ({ct.get('Status')})"
    have = sorted(n.lstrip("/") for ct in data for n in (ct.get("Names") or []))
    near = [h for h in have if name.split("-")[-1][:4] in h][:4]
    return False, (f"{name} 컨테이너가 없다"
                   + (f" — 비슷한 이름: {', '.join(near)}" if near else ""))


async def container_network(spec: dict) -> tuple[bool, str]:
    name, net, cidr = spec["name"], spec["network"], spec.get("cidr", "")
    st, data = await _docker(f"/containers/{name}/json")
    if st != 200:
        return False, f"{name} 을 찾을 수 없다"
    nets = ((data.get("NetworkSettings") or {}).get("Networks") or {})
    hit = {k: v for k, v in nets.items() if net in k}
    if not hit:
        return False, (f"{name} 이 {net} 망에 없다 — 붙어 있는 망: "
                       f"{', '.join(nets) or '없음'}")
    ip = next(iter(hit.values())).get("IPAddress", "")
    if cidr and not ip.startswith(cidr):
        return False, f"{name} 의 주소가 {ip} 다 — {cidr}x 대역이어야 한다"
    return True, f"{name} → {net} {ip}"


async def asset_in_ledger(spec: dict) -> tuple[bool, str]:
    """자산 대장에 등재됐는가.

    컨테이너만 띄우고 대장에 안 올리는 것이 가장 흔한 누락이다. 대장에 없으면
    관제 화면에 안 보이고, 경보가 안 붙고, 사고 때 아무도 그것이 존재하는지 모른다.
    """
    st, data = await _envsim("/assets")
    if st != 200:
        return False, "envsim 자산 대장을 읽을 수 없다"
    want = spec["asset_id"]
    # 대장은 두 층이다: 최상위 리스트(it_assets·racks…)와 facility 아래의 계통별
    # 리스트(ups·crac…). 한 층만 훑으면 시설 자산이 통째로 안 걸린다.
    def walk(node, path=""):
        if isinstance(node, list):
            for it in node:
                if isinstance(it, dict) and it.get("id") == want:
                    yield path, it
        elif isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}" if path else k)

    found = next(walk(data), None)
    if not found:
        return False, f"대장에 {want} 가 없다 — 컨테이너만 띄우고 등재하지 않았는가"
    group, it = found
    for k, v in (spec.get("fields") or {}).items():
        if str(it.get(k, "")) != str(v):
            return False, f"{want} 의 {k} 가 {it.get(k)!r} 다 — {v!r} 여야 한다"
    return True, f"{want} 등재됨 ({group}, floor={it.get('floor')}, zone={it.get('zone')})"


async def power_headroom(spec: dict) -> tuple[bool, str]:
    """임계전력 대비 여유. 증설의 진짜 제약은 랙 공간이 아니라 전력이다."""
    st, data = await _envsim("/state")
    if st != 200:
        return False, "envsim 상태를 읽을 수 없다"
    eff = data.get("efficiency") or {}
    pct = float(eff.get("critical_power_pct", 0))
    need = float(spec.get("max_pct", 85))
    ok = pct <= need
    return ok, (f"임계전력 사용률 {pct:.1f}% (상한 {need:.0f}%)"
                + ("" if ok else " — 증설분을 감당할 여유가 없다. 부하를 옮기거나 상한을 재검토하라"))


async def thermal_margin(spec: dict) -> tuple[bool, str]:
    st, data = await _envsim("/state")
    if st != 200:
        return False, "envsim 상태를 읽을 수 없다"
    temps = [a.get("temp_c") for a in (data.get("aisles") or []) if a.get("temp_c") is not None]
    if not temps:
        return False, "아일 온도를 읽을 수 없다"
    hot = max(temps)
    limit = float(spec.get("max_c", 27))
    ok = hot <= limit
    return ok, f"최고 아일 온도 {hot:.1f}°C (상한 {limit:.0f}°C)" + ("" if ok else " — 냉방이 못 따라간다")


async def file_contains(spec: dict) -> tuple[bool, str]:
    p = REPO / spec["path"]
    if not p.exists():
        return False, f"{spec['path']} 가 없다"
    text = p.read_text(encoding="utf-8", errors="replace")
    pat = spec["pattern"]
    ok = bool(re.search(pat, text, re.M))
    return ok, (f"{spec['path']} 에서 확인" if ok
                else f"{spec['path']} 에 {pat!r} 가 없다 — 저장소에 반영하지 않으면 "
                     f"다음 배포에서 사라진다")


async def file_absent(spec: dict) -> tuple[bool, str]:
    p = REPO / spec["path"]
    if not p.exists():
        return True, f"{spec['path']} 없음"
    text = p.read_text(encoding="utf-8", errors="replace")
    hit = re.search(spec["pattern"], text, re.M)
    return (not hit), (f"{spec['path']} 에 아직 {spec['pattern']!r} 가 남아 있다"
                       if hit else f"{spec['path']} 에서 제거됨")


CHECKS = {
    "container_running": container_running,
    "container_network": container_network,
    "asset_in_ledger": asset_in_ledger,
    "power_headroom": power_headroom,
    "thermal_margin": thermal_margin,
    "file_contains": file_contains,
    "file_absent": file_absent,
}


async def run(spec: dict) -> dict:
    fn = CHECKS.get(spec.get("type"))
    if not fn:
        return {"passed": False, "detail": f"모르는 검사 종류: {spec.get('type')}"}
    try:
        ok, detail = await fn(spec)
    except Exception as e:
        ok, detail = False, f"검사 중 오류: {type(e).__name__}: {e}"
    return {"passed": ok, "detail": detail}
