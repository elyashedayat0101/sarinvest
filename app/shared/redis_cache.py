"""
app/shared/redis_cache.py
============================
A generic "cache one Pydantic model per key, with TTL" utility backed by
Redis — same `get`/`set` shape as `shared/cache.py::TTLCache` so service
code reads the same regardless of which backend is wired in, but this
one is safe across multiple processes/instances (unlike `TTLCache`,
which is process-local).

This is a second real use of Redis in this app (the first was
`domains/users/otp_store.py`) — different enough in shape that it's a
separate utility rather than a shared abstraction with that one:
`otp_store.py` needs atomic increment and a two-key (data + attempts)
layout; this is a plain single-value cache. Don't force them into one
interface just because they both touch Redis — see ARCHITECTURE.md's
caching section for the general principle.

Reuses whatever `redis.asyncio.Redis` client `main.py`'s lifespan
already created for the `users` domain — one Redis connection for the
whole app, not one per domain that happens to want caching.
"""
from __future__ import annotations

from typing import Generic, Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class RedisCache(Generic[T]):
    def __init__(self, redis_client, namespace: str, ttl_seconds: float, model: Type[T]):
        self._redis = redis_client
        self._namespace = namespace
        self._ttl = int(ttl_seconds)
        self._model = model

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    async def get(self, key: str) -> Optional[T]:
        raw = await self._redis.get(self._key(key))
        if raw is None:
            return None
        return self._model.model_validate_json(raw)

    async def set(self, key: str, value: T) -> None:
        await self._redis.set(self._key(key), value.model_dump_json(), ex=self._ttl)

    async def invalidate(self, key: str) -> None:
        await self._redis.delete(self._key(key))
