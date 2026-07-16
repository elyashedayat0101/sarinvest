"""
app/shared/cache.py
=====================
A deliberately small in-memory TTL cache — no Redis dependency introduced
for this. It's the right amount of caching for "smooth out a burst of
requests between poll cycles" on a single instance; it is NOT a
substitute for Redis if you ever run more than one API process (each
process would have its own independent cache, which is fine for this use
case — worst case, two processes each make one extra exchange call — but
would be wrong for anything requiring cache *consistency* across
instances, e.g. rate-limit counters). See ARCHITECTURE.md for when to
graduate to Redis.

Uses `asyncio.Lock`, not `threading.Lock` — unlike `AppState` (written
from a background OS thread, see services/state.py), this cache is only
ever touched from async code running on the event loop, so `asyncio.Lock`
is the correct, non-blocking choice here.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: float):
        self._ttl = ttl_seconds
        self._store: dict[str, _Entry[T]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[T]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._store[key]
                return None
            return entry.value

    async def set(self, key: str, value: T) -> None:
        async with self._lock:
            self._store[key] = _Entry(value=value, expires_at=time.monotonic() + self._ttl)

    async def invalidate(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)
