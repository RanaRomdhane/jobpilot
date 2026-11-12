"""Response cache: identical (provider, model, messages, params) requests are answered once."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.asyncio import Redis

# `jobpilot_security.spotlight` tags each untrusted block with a random nonce, so the same request
# never produces the same text twice. The nonce protects the prompt, it does not change the
# question, so the key ignores it. Matched in the JSON below, hence the escaped quote. Untrusted
# data cannot fake this tag: spotlight rewrites "untrusted_data" inside the data.
_SPOTLIGHT_NONCE = re.compile(r'(</?untrusted_data nonce=\\")[0-9a-f]+')


def cache_key(provider: str, model: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"provider": provider, "model": model, "payload": payload},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    canonical = _SPOTLIGHT_NONCE.sub(r"\1", canonical)
    return "llmcache:" + hashlib.sha256(canonical.encode()).hexdigest()


class ResponseCache:
    def __init__(self, redis: Redis, ttl_s: int) -> None:
        self._redis = redis
        self._ttl_s = ttl_s

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    async def set(self, key: str, value: dict[str, Any]) -> None:
        await self._redis.set(key, json.dumps(value, ensure_ascii=False), ex=self._ttl_s)
