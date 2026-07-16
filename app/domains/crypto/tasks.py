"""
app/domains/crypto/tasks.py
==============================
Deliberately mirrors `app/services/fetch_service.py`'s shape (asyncio.Task
started in lifespan, interruptible sleep via CancelledError) rather than
inventing a new background-task pattern — one polling-loop idiom for the
whole app, not one per domain.

Unlike the market fetch loop, exchange HTTP calls here are natively async
(httpx.AsyncClient), so this task runs directly on the event loop with no
`asyncio.to_thread` wrapping needed — there's no blocking synchronous
call hiding inside it.
"""
from __future__ import annotations

import asyncio
import logging

from app.domains.crypto.service import CryptoPriceService

log = logging.getLogger("crypto.tasks")


class CryptoPollingTask:
    def __init__(self, service: CryptoPriceService, symbols: list[str], interval_seconds: float):
        self._service = service
        self._symbols = symbols
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="crypto-poll-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        log.info("crypto polling loop started — symbols=%s interval=%ss", self._symbols, self._interval)
        try:
            while True:
                for symbol in self._symbols:
                    try:
                        # use_cache=False: this loop's whole job is to
                        # refresh the cache/DB, not read from it
                        await self._service.get_latest_unified(symbol, use_cache=False)
                    except Exception as e:
                        log.error("poll failed for %s: %s", symbol, e)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            log.info("crypto polling loop cancelled — shutting down")
            raise
