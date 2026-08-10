"""docker.sock 얇은 클라이언트.

주입기는 랩을 실제로 망가뜨리는 유일한 서비스다. 그래서 여기 있는 함수는 전부
**되돌릴 수 있는 것만** 노출한다 — 컨테이너를 지우거나 볼륨을 건드리는 API 는 아예
감싸지 않았다. 되돌릴 수 없는 조작은 수업을 복구 불가능하게 만든다.
"""
from __future__ import annotations

import json as _json
import logging
import os

import httpx

log = logging.getLogger("injector.dk")
SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=SOCK),
                             base_url="http://docker", timeout=timeout)


async def api(method: str, path: str, *, body=None, timeout: float = 30.0):
    async with _client(timeout) as c:
        r = await c.request(method, path, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"docker {method} {path} → {r.status_code} {r.text[:200]}")
        if not r.content:
            return None
        try:
            return r.json()
        except Exception:
            return r.text


async def containers() -> list[dict]:
    return await api("GET", "/containers/json?all=true")


async def inspect(name: str) -> dict:
    return await api("GET", f"/containers/{name}/json")


async def state_of(name: str) -> str:
    try:
        return (await inspect(name))["State"]["Status"]
    except Exception:
        return "unknown"


# ── 생명주기 ────────────────────────────────────────────────────────
async def stop(name: str, t: int = 8):
    await api("POST", f"/containers/{name}/stop?t={t}", timeout=t + 20)


async def start(name: str):
    await api("POST", f"/containers/{name}/start")


async def kill(name: str, sig: str = "SIGKILL"):
    await api("POST", f"/containers/{name}/kill?signal={sig}")


async def pause(name: str):
    await api("POST", f"/containers/{name}/pause")


async def unpause(name: str):
    await api("POST", f"/containers/{name}/unpause")


async def update(name: str, **res):
    """자원 제한.

    ⚠ **0 은 '해제'가 아니라 '변경 없음'이다.** 도커 update API 는 0 을 "지정하지
    않음"으로 읽고 조용히 무시한다 — 오류도 경고도 없이 `{"Warnings":null}` 을
    돌려주므로, 원복이 성공한 것처럼 보이면서 제한은 그대로 남는다. 실측:

      NanoCPUs=0        → cgroup cpu.max 이 `5000 100000` 그대로 (해제 안 됨)
      CpuQuota=-1       → `max 100000` 로 진짜 풀림  ← CPU 는 이걸 써야 한다
      Memory=0          → memory.max 그대로 (해제 안 됨)
      Memory=-1         → 거부: "Minimum memory limit allowed is 6MB"
      Memory=아주 큰 값 → 거부: "memory+swap limit should be >= memory limit"

    메모리 제한은 update 로 **없앨 수 없다**. 올리는 것만 된다. 완전한 원복은
    컨테이너 재생성뿐이라, mem_limit 의 revert 는 사실상 무한대(호스트 RAM)까지
    올리는 것으로 대신한다. clear_mem_limit 참고.
    """
    await api("POST", f"/containers/{name}/update", body=res)


async def clear_cpu_limit(name: str):
    """CPU 제한 해제. NanoCPUs=0 이 아니라 CpuQuota=-1 이어야 실제로 풀린다."""
    await update(name, CpuQuota=-1, CpuPeriod=0)


def _host_ram() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 32 * 1024 ** 3


async def clear_mem_limit(name: str):
    """메모리 제한 '해제'. 실제로는 호스트 RAM 만큼 올리는 것이다 — update API 로는
    제한을 없앨 수 없다. 실사용 관점에서는 무제한과 같지만, inspect 의 Memory 는
    0 이 아니라 그 큰 값으로 남는다. 완전히 지우려면 컨테이너를 재생성해야 한다."""
    r = _host_ram()
    await update(name, Memory=r, MemorySwap=r)


# ── 네트워크 ────────────────────────────────────────────────────────
async def net_of(name: str) -> dict[str, str]:
    """이 컨테이너가 붙은 네트워크 -> 고정 IP. 끊기 전에 반드시 기록해야 한다.
    IP 를 잃어버리면 다시 붙일 때 주소가 바뀌고 존 체계가 통째로 어긋난다."""
    nets = (await inspect(name))["NetworkSettings"]["Networks"]
    return {k: v.get("IPAddress", "") for k, v in nets.items()}


async def net_disconnect(name: str, net: str):
    await api("POST", f"/networks/{net}/disconnect", body={"Container": name, "Force": True})


async def net_connect(name: str, net: str, ip: str | None = None):
    cfg: dict = {"Container": name}
    if ip:
        cfg["EndpointConfig"] = {"IPAMConfig": {"IPv4Address": ip}}
    await api("POST", f"/networks/{net}/connect", body=cfg)


# ── exec ───────────────────────────────────────────────────────────
async def sh(name: str, script: str, *, detach: bool = False, timeout: float = 60.0) -> str:
    """컨테이너 안에서 sh 한 줄. detach 면 붙지 않고 계속 돈다(부하 발생기용)."""
    create = await api("POST", f"/containers/{name}/exec", body={
        "Cmd": ["sh", "-c", script],
        "AttachStdout": not detach, "AttachStderr": not detach, "Tty": False,
    })
    eid = create["Id"]
    async with _client(timeout) as c:
        r = await c.post(f"/exec/{eid}/start",
                         content=_json.dumps({"Detach": detach, "Tty": False}),
                         headers={"Content-Type": "application/json"})
        if r.status_code >= 400:
            raise RuntimeError(f"exec {name} → {r.status_code} {r.text[:200]}")
        if detach:
            return ""
        # 멀티플렉스 스트림 — 8바이트 헤더를 걷어낸다
        out, buf = [], r.content
        i = 0
        while i + 8 <= len(buf):
            n = int.from_bytes(buf[i + 4:i + 8], "big")
            out.append(buf[i + 8:i + 8 + n].decode("utf-8", "replace"))
            i += 8 + n
        return ("".join(out) if out else buf.decode("utf-8", "replace")).strip()
