"""
app/domains/commodities/tasks.py
===================================
Same shape as `domains/crypto/tasks.py::CryptoPollingTask` — one polling
loop idiom for the whole app.
"""
from __future__ import annotations

import asyncio
import logging

from app.domains.commodities.service import CommodityService

log = logging.getLogger("commodities.tasks")


class CommodityPollingTask:
    def __init__(self, service: CommodityService, groups: list[str], interval_seconds: float):
        self._service = service
        self._groups = groups
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="commodities-poll-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        log.info("commodities polling loop started — groups=%s interval=%ss", self._groups, self._interval)
        try:
            while True:
                for group in self._groups:
                    try:
                        await self._service.get_all(group, use_cache=False)
                    except Exception as e:
                        log.error("poll failed for group=%s: %s", group, e)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            log.info("commodities polling loop cancelled — shutting down")
            raise
