"""자산 대장에 명시된 SSH 포트의 접속성만 확인한다. 로그인·모델 호출은 하지 않는다."""
import asyncio
import ipaddress
import time


class RemoteHosts:
    def __init__(self, ttl=60, connect=None, clock=time.time):
        self.ttl = ttl
        self.connect = connect or self._connect
        self.clock = clock
        self.cache = {}
        self.lock = asyncio.Lock()

    @staticmethod
    async def _connect(host, port):
        writer = None
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
            return True
        except (OSError, asyncio.TimeoutError):
            return False
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    async def collect(self, assets):
        targets = {}
        for asset in assets:
            config = asset.get("availability", {})
            if config.get("kind") != "ssh_tcp":
                continue
            try:
                host = str(ipaddress.ip_address(asset["ip"]))
                port = config["port"]
                if type(port) is not int or not 1 <= port <= 65535:
                    continue
                targets[asset["id"]] = (host, port)
            except (KeyError, ValueError, TypeError):
                continue
        async with self.lock:
            now = self.clock()

            async def one(asset_id, endpoint):
                hit = self.cache.get(asset_id)
                if hit and hit["endpoint"] == endpoint and now - hit["checked_at"] < self.ttl:
                    return
                reachable = await self.connect(*endpoint)
                self.cache[asset_id] = dict(endpoint=endpoint, reachable=reachable,
                    checked_at=self.clock(), source="SSH TCP 접속성", interval_sec=self.ttl)

            await asyncio.gather(*(one(key, endpoint) for key, endpoint in targets.items()))
            self.cache = {key: value for key, value in self.cache.items() if key in targets}
            return {key: {k: v for k, v in row.items() if k != "endpoint"}
                    for key, row in self.cache.items()}
