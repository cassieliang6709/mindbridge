"""Redis-backed query cache.

Scope note: this is an **exact-key** cache — the key is a hash of the
normalised query plus its parameters. It stops the identical question being
re-embedded and re-queried. It is not yet the semantic cache from M5, which
matches on embedding proximity above a tuned threshold; that lands with M5 and
its own false-hit measurement. Naming it accurately here keeps the
cacheCostSaving metric honest when it is finally measured.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import redis.asyncio as redis

from .settings import Settings

logger = logging.getLogger(__name__)


class QueryCache:
    def __init__(self, client: redis.Redis | None, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self.hits = 0
        self.misses = 0

    @classmethod
    async def connect(cls, settings: Settings) -> "QueryCache":
        if not settings.cache_enabled:
            return cls(None, settings.cache_ttl_seconds)
        client: redis.Redis = redis.from_url(
            str(settings.redis_url), decode_responses=True
        )
        try:
            await client.ping()
        except Exception as error:  # pragma: no cover - depends on deployment
            logger.warning("redis unavailable (%s); running without cache", error)
            await client.aclose()
            return cls(None, settings.cache_ttl_seconds)
        return cls(client, settings.cache_ttl_seconds)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @staticmethod
    def key(namespace: str, payload: dict[str, Any]) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.blake2b(blob.encode(), digest_size=16).hexdigest()
        return f"mindbridge:{namespace}:{digest}"

    async def get(self, key: str) -> dict[str, Any] | None:
        if self._client is None:
            return None
        raw = await self._client.get(key)
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(raw)

    async def set(self, key: str, value: dict[str, Any]) -> None:
        if self._client is None:
            return
        await self._client.set(
            key, json.dumps(value, ensure_ascii=False, default=str), ex=self._ttl
        )

    async def invalidate_namespace(self, namespace: str) -> int:
        """Drop cached reads after a write, so a new preference is visible."""
        if self._client is None:
            return 0
        removed = 0
        async for key in self._client.scan_iter(f"mindbridge:{namespace}:*"):
            removed += await self._client.delete(key)
        return removed

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
