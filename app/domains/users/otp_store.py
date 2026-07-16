"""
app/domains/users/otp_store.py
=================================
OTP codes are ephemeral, short-TTL data — a much better fit for Redis
than a SQLite table. Storing them in the app database (an earlier version
of this domain did) means: rows accumulate forever unless something
cleans them up, "is this still valid" requires an application-level
`expires_at` comparison instead of the store just not having the key
anymore, and every OTP write contends with the same SQLite file every
other domain uses. Redis's native key TTL solves all three for free.

Two implementations, same shape as `OtpSender`'s dev-vs-real split:

- `RedisOtpStore` — the real one. Two keys per (phone, purpose): a JSON
  blob for the code hash + timestamps (TTL = otp_expire_seconds, so an
  expired OTP simply stops existing — no manual cleanup job needed), and
  a separate integer key for attempt count, incremented via Redis's
  atomic `INCR` rather than a read-modify-write on the JSON blob (which
  would have a race: two concurrent wrong-code requests could both read
  attempt_count=4, both increment to 5, and both then be allowed a 6th
  try instead of being capped at 5).
- `InMemoryOtpStore` — dev-only fallback when `settings.redis_url` isn't
  configured, so `pip install -r requirements.txt && uvicorn ...` works
  without also standing up Redis. Never use this in production — it's
  process-local, so it breaks the moment you run more than one worker,
  and everything resets on restart.
"""
from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class OtpRecord:
    code_hash: str
    created_at: str
    expires_at: str
    attempt_count: int


def _key(phone_number: str, purpose: str) -> str:
    return f"otp:{phone_number}:{purpose}"


class OtpStore(ABC):
    @abstractmethod
    async def save(self, phone_number: str, purpose: str, code_hash: str, created_at: str, expires_at: str, ttl_seconds: int) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get(self, phone_number: str, purpose: str) -> Optional[OtpRecord]:
        raise NotImplementedError

    @abstractmethod
    async def increment_attempts(self, phone_number: str, purpose: str) -> int:
        """Atomically increments and returns the new attempt count."""
        raise NotImplementedError

    @abstractmethod
    async def consume(self, phone_number: str, purpose: str) -> None:
        """Deletes the record — called once a code is successfully verified,
        so it can never be replayed even if it hasn't expired yet."""
        raise NotImplementedError


class RedisOtpStore(OtpStore):
    def __init__(self, redis_client):
        # Typed as `Any`-ish deliberately (no import of `redis.asyncio` in
        # the type signature) so this module doesn't hard-require the
        # `redis` package to even *import* — only to actually run with
        # this class instantiated. Callers pass a `redis.asyncio.Redis`
        # instance from main.py's lifespan.
        self._redis = redis_client

    async def save(self, phone_number: str, purpose: str, code_hash: str, created_at: str, expires_at: str, ttl_seconds: int) -> None:
        key = _key(phone_number, purpose)
        data = json.dumps({"code_hash": code_hash, "created_at": created_at, "expires_at": expires_at})
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(f"{key}:data", data, ex=ttl_seconds)
            pipe.set(f"{key}:attempts", 0, ex=ttl_seconds)
            await pipe.execute()

    async def get(self, phone_number: str, purpose: str) -> Optional[OtpRecord]:
        key = _key(phone_number, purpose)
        raw, attempts = await asyncio.gather(
            self._redis.get(f"{key}:data"), self._redis.get(f"{key}:attempts")
        )
        if raw is None:
            return None
        data = json.loads(raw)
        return OtpRecord(
            code_hash=data["code_hash"], created_at=data["created_at"], expires_at=data["expires_at"],
            attempt_count=int(attempts) if attempts is not None else 0,
        )

    async def increment_attempts(self, phone_number: str, purpose: str) -> int:
        key = f"{_key(phone_number, purpose)}:attempts"
        return await self._redis.incr(key)

    async def consume(self, phone_number: str, purpose: str) -> None:
        key = _key(phone_number, purpose)
        await self._redis.delete(f"{key}:data", f"{key}:attempts")


class InMemoryOtpStore(OtpStore):
    """Dev-only — see module docstring. Same `asyncio.Lock`-per-store
    approach as `shared/cache.py::TTLCache`, for the same reason: every
    caller here is on the event loop, never a background OS thread."""

    def __init__(self):
        self._store: dict[str, tuple[OtpRecord, float]] = {}  # key -> (record, expires_at_monotonic)
        self._lock = asyncio.Lock()

    async def save(self, phone_number: str, purpose: str, code_hash: str, created_at: str, expires_at: str, ttl_seconds: int) -> None:
        async with self._lock:
            key = _key(phone_number, purpose)
            self._store[key] = (
                OtpRecord(code_hash=code_hash, created_at=created_at, expires_at=expires_at, attempt_count=0),
                time.monotonic() + ttl_seconds,
            )

    async def get(self, phone_number: str, purpose: str) -> Optional[OtpRecord]:
        async with self._lock:
            key = _key(phone_number, purpose)
            entry = self._store.get(key)
            if entry is None:
                return None
            record, expires_at_mono = entry
            if time.monotonic() >= expires_at_mono:
                del self._store[key]
                return None
            return record

    async def increment_attempts(self, phone_number: str, purpose: str) -> int:
        async with self._lock:
            key = _key(phone_number, purpose)
            entry = self._store.get(key)
            if entry is None:
                return 0
            record, expires_at_mono = entry
            record.attempt_count += 1
            self._store[key] = (record, expires_at_mono)
            return record.attempt_count

    async def consume(self, phone_number: str, purpose: str) -> None:
        async with self._lock:
            self._store.pop(_key(phone_number, purpose), None)
