"""
app/domains/commodities/repository.py
========================================
Same discipline as every other domain's repository — no business logic,
short-lived session per method.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.utils import model_to_dict
from app.domains.commodities.models import CommodityPriceSnapshot
from app.domains.commodities.schemas import RawCommodityPrice


class CommodityRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def save_many(self, prices: List[RawCommodityPrice]) -> None:
        if not prices:
            return
        async with self._session_factory() as session:
            async with session.begin():
                for p in prices:
                    session.add(CommodityPriceSnapshot(
                        ins_code=p.ins_code, isin=p.isin, group=p.group,
                        short_name=p.short_name, full_name=p.full_name,
                        last_price=p.last_price, closing_price=p.closing_price,
                        previous_close=p.previous_close, open_price=p.open_price,
                        day_low=p.day_low, day_high=p.day_high,
                        change_amount=p.change_amount, change_percent=p.change_percent,
                        volume=p.volume, value=p.value, trade_count=p.trade_count,
                        nav=p.nav, week_low=p.week_low, week_high=p.week_high,
                        year_low=p.year_low, year_high=p.year_high,
                        redemption_price=p.redemption_price, subscription_price=p.subscription_price,
                        fetched_at=p.fetched_at.isoformat(),
                    ))

    async def get_latest_per_instrument(self, group: str) -> List[dict]:
        async with self._session_factory() as session:
            rows = (await session.execute(
                select(CommodityPriceSnapshot)
                .where(CommodityPriceSnapshot.group == group)
                .order_by(CommodityPriceSnapshot.fetched_at.desc())
            )).scalars().all()

            seen: set[str] = set()
            latest: List[dict] = []
            for row in rows:
                if row.ins_code in seen:
                    continue
                seen.add(row.ins_code)
                latest.append(model_to_dict(row))
            return latest

    async def get_latest_for_instrument(self, ins_code: str) -> Optional[dict]:
        async with self._session_factory() as session:
            row = (await session.execute(
                select(CommodityPriceSnapshot)
                .where(CommodityPriceSnapshot.ins_code == ins_code)
                .order_by(CommodityPriceSnapshot.fetched_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            return model_to_dict(row) if row else None
