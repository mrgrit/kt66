"""NOC와 envsim이 공유하는 읽기 전용 모델 목록 수집기. 추론·SSH 로그인은 하지 않는다."""
import asyncio
import ipaddress
import json
import time

import httpx


class AIServiceInventory:
    PLATFORMS = {"ollama", "vllm", "llama.cpp", "openai", "health"}

    def __init__(self, ttl=60, transport=None, clock=time.time):
        self.ttl, self.transport, self.clock = ttl, transport, clock
        self.cache, self.locks = {}, {}

    @staticmethod
    async def _get(client, base, path):
        try:
            async with client.stream("GET", base + path) as response:
                if response.status_code != 200:
                    status = response.status_code
                    return None, "인증 필요" if status in (401, 403) else f"HTTP {status} · 조회 불가"
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        return None, "응답 크기 제한 초과"
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    return None, "지원하지 않는 응답 형식"
                return payload, None
        except httpx.TimeoutException:
            return None, "응답 시간 초과"
        except httpx.HTTPError:
            return None, "관제 서버에서 연결 불가"
        except (ValueError, UnicodeError):
            return None, "JSON 응답 확인 불가"

    @staticmethod
    def _models(data, key, ollama=False):
        rows = data.get(key) if isinstance(data, dict) else None
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            return None
        result = []
        for row in rows:
            name = row.get("name" if ollama else "id")
            if not isinstance(name, str) or not name:
                return None
            item = {"name": name[:300]}
            detail = row.get("details") or {}
            if not isinstance(detail, dict):
                detail = {}
            for field in ("family", "parameter_size", "quantization_level"):
                if isinstance(detail.get(field), str):
                    item[field] = detail[field][:80]
            for field in ("size", "size_vram", "context_length", "max_model_len"):
                if type(row.get(field)) in (int, float) and 0 <= row[field] < 1e18:
                    item[field] = row[field]
            if isinstance(row.get("expires_at"), str):
                item["expires_at"] = row["expires_at"][:100]
            if isinstance(row.get("root"), str):
                item["base_model"] = row["root"].rstrip("/").rsplit("/", 1)[-1][:300]
            result.append(item)
        return result

    async def _service(self, client, host, config):
        platform, port = config.get("platform"), config.get("port")
        row = {"id": str(config.get("id", ""))[:80], "platform": platform,
               "name": str(config.get("name", platform or "서비스"))[:120],
               "purpose": str(config.get("purpose", ""))[:200], "models": None, "loaded": None}
        if platform not in self.PLATFORMS or type(port) is not int or not 1 <= port <= 65535:
            return dict(row, status="설정 오류", catalog_error="지원 플랫폼과 포트를 확인하세요")
        base = f"http://{'['+host+']' if ':' in host else host}:{port}"
        row["endpoint"] = base
        paths = ["/api/tags", "/api/ps", "/api/version"] if platform == "ollama" else ["/health"] if platform == "health" else ["/v1/models"]
        answers = await asyncio.gather(*(self._get(client, base, p) for p in paths))
        data, error = answers[0]
        if platform == "health":
            ok = data and data.get("ok") is True
            name = data.get("model") if ok else None
            models = [{"name": name.rstrip("/").rsplit("/", 1)[-1][:300]}] if isinstance(name, str) else None
            row.update(models=models, status="응답 확인" if ok else error or "정상 응답 미확인")
        else:
            models = self._models(data, "models" if platform == "ollama" else "data", platform == "ollama")
            row.update(models=models, status="응답 확인" if models is not None else error or "모델 목록 형식 미확인")
            if platform == "ollama":
                loaded, ps_error = answers[1]
                row["loaded"] = self._models(loaded, "models", True)
                if row["loaded"] is None:
                    row["loaded_error"] = ps_error or "적재 목록 형식 미확인"
                version, _ = answers[2]
                if version and isinstance(version.get("version"), str):
                    row["version"] = version["version"][:80]
                if models is None and row["loaded"] is not None:
                    row["status"] = "일부 응답 확인"
        if row["models"] is None:
            row["catalog_error"] = error or "모델 목록 미확인"
        # 실제 적재 중인 모델을 먼저 제공한다. 설치 목록과 적재 상태를 섞지 않는다.
        loaded_names = {m["name"] for m in row["loaded"] or []}
        if row["models"] is not None:
            row["model_count"] = len(row["models"])
            row["models"] = sorted(row["models"], key=lambda m: (m["name"] not in loaded_names, m["name"]))[:200]
        if row["loaded"] is not None:
            row["loaded_count"] = len(row["loaded"])
            row["loaded"] = row["loaded"][:200]
        row["checked_at"] = self.clock()
        return row

    async def collect(self, asset):
        key = asset["id"]
        try:
            host = str(ipaddress.ip_address(asset.get("remote") or asset["ip"]))
        except (KeyError, TypeError, ValueError):
            return {"asset_id": key, "services": [], "error": "자산 대장의 IP 설정을 확인하세요"}
        configs = asset.get("serving", [])
        if not isinstance(configs, list):
            configs = []
        configs = [c for c in configs[:12] if isinstance(c, dict)]
        fingerprint = json.dumps([host, configs], sort_keys=True)
        async with self.locks.setdefault(key, asyncio.Lock()):
            hit = self.cache.get(key)
            if hit and hit[0] == fingerprint and self.clock() - hit[1]["checked_at"] < self.ttl:
                return hit[1]
            async with httpx.AsyncClient(timeout=4, follow_redirects=False, trust_env=False,
                                         transport=self.transport) as client:
                services = await asyncio.gather(*(self._service(client, host, c) for c in configs))
            result = {"asset_id": key, "checked_at": self.clock(), "interval_sec": self.ttl,
                      "services": services, "source": "등록된 서비스의 읽기 전용 HTTP API"}
            self.cache[key] = fingerprint, result
            return result
