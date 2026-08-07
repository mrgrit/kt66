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
    """자원 제한. 0 을 주면 해제된다 — 그래서 원복이 항상 가능하다."""
    await api("POST", f"/containers/{name}/update", body=res)


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
