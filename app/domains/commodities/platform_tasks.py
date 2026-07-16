"""
app/domains/commodities/platform_tasks.py
==============================================
Same shape as every other polling task in this app. Default interval is
60 seconds (`settings.gold_platform_poll_interval`) — matches "call these
every 1 min" exactly.
"""
from __future__ import annotations

import asyncio
import logging

from app.domains.commodities.platform_service import GoldPlatformPriceService

log = logging.getLogger("commodities.platform_tasks")


class GoldPlatformPollingTask:
    def __init__(self, service: GoldPlatformPriceService, interval_seconds: float):
        self._service = service
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="gold-platform-poll-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        log.info("gold platform polling loop started — interval=%ss", self._interval)
        try:
            while True:
                try:
                    await self._service.get_all(use_cache=False)
                except Exception as e:
                    log.error("poll failed: %s", e)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            log.info("gold platform polling loop cancelled — shutting down")
            raise
