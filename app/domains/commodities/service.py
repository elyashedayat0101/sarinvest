"""
app/domains/commodities/service.py
=====================================
Same shape as `domains/crypto/service.py`: fetch concurrently, tolerate
partial failure, cache, persist. `get_today_changes` doesn't re-fetch —
it reuses whatever `get_all` most recently cached/returned, since "today's
change" is just a different view (sorted by change_percent) over the
same data, not a separate data source.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from app.domains.commodities.clients.base import CommodityDataClient
from app.domains.commodities.exceptions import AllInstrumentsUnavailableError, UnknownGroupError, UnknownInstrumentError
from app.domains.commodities.registry import group_for_ins_code, instruments_for_group
from app.domains.commodities.repository import CommodityRepository
from app.domains.commodities.schemas import (
    CommodityErrorOut, CommodityListOut, CommodityOut, RawCommodityPrice,
    TodayChangeItemOut, TodayChangesOut,
)
from app.shared.cache import TTLCache

log = logging.getLogger("commodities.service")


class CommodityService:
    def __init__(self, client: CommodityDataClient, repo: CommodityRepository, cache: TTLCache[CommodityListOut]):
        self._client = client
        self._repo = repo
        self._cache = cache

    async def get_all(self, group: str, use_cache: bool = True) -> CommodityListOut:
        instruments = instruments_for_group(group)  # type: ignore[arg-type]
        if not instruments:
            raise UnknownGroupError(f"هیچ نمادی برای گروه '{group}' ثبت نشده است")

        if use_cache:
            cached = await self._cache.get(group)
            if cached is not None:
                return cached.model_copy(update={"from_cache": True})

        successes, errors = await self._client.fetch_many(instruments)
        if not successes:
            raise AllInstrumentsUnavailableError(f"دریافت اطلاعات هیچ نمادی برای گروه '{group}' موفق نبود")

        now_iso = datetime.now(timezone.utc).isoformat()
        result = CommodityListOut(
            group=group,
            instruments=[_to_out(p) for p in successes],
            errors=[CommodityErrorOut(ins_code=code, error=msg) for code, msg in errors],
            fetched_at=now_iso,
            from_cache=False,
        )

        await self._cache.set(group, result)
        await self._repo.save_many(successes)
        return result

    async def get_one(self, ins_code: str) -> CommodityOut:
        group = group_for_ins_code(ins_code)
        if group is None:
            raise UnknownInstrumentError(f"نماد '{ins_code}' در فهرست شناخته‌شده نیست")

        instruments = [i for i in instruments_for_group(group) if i.ins_code == ins_code]
        successes, errors = await self._client.fetch_many(instruments)
        if not successes:
            code, msg = errors[0]
            raise AllInstrumentsUnavailableError(f"دریافت اطلاعات نماد '{ins_code}' ناموفق بود: {msg}")
        return _to_out(successes[0])

    async def get_today_changes(self, group: str) -> TodayChangesOut:
        listing = await self.get_all(group, use_cache=True)
        items = [
            TodayChangeItemOut(
                ins_code=i.ins_code, isin=i.isin, short_name=i.short_name,
                last_price=i.last_price, change_amount=i.change_amount,
                change_percent=i.change_percent, volume=i.volume,
            )
            for i in listing.instruments
        ]
        items.sort(key=lambda i: (i.change_percent if i.change_percent is not None else float("-inf")), reverse=True)
        return TodayChangesOut(group=group, items=items, fetched_at=listing.fetched_at)


def _to_out(p: RawCommodityPrice) -> CommodityOut:
    return CommodityOut(**p.model_dump(exclude={"fetched_at"}), fetched_at=p.fetched_at.isoformat())
