"""
app/domains/crypto/repository.py
===================================
Same discipline as market_repo.py/portfolio_repo.py: each method opens
its own short-lived AsyncSession and commits immediately. No business
logic here — aggregation lives in aggregator.py, orchestration (calling
exchanges, caching, persisting) lives in service.py. This file only knows
how to read/write `crypto_price_snapshots`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.utils import model_to_dict
from app.domains.crypto.models import PriceSnapshot
from app.domains.crypto.schemas import RawPrice


class CryptoRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def save_many(self, prices: List[RawPrice]) -> None:
        if not prices:
            return
        async with self._session_factory() as session:
            async with session.begin():
                for p in prices:
                    session.add(PriceSnapshot(
                        exchange=p.exchange, symbol=p.symbol, price=p.price,
                        bid=p.bid, ask=p.ask, volume_24h=p.volume_24h,
                        fetched_at=p.fetched_at.isoformat(),
                    ))

    async def get_latest_per_exchange(self, symbol: str) -> List[dict]:
        """Most recent snapshot for `symbol`, one row per exchange."""
        async with self._session_factory() as session:
            subq = (
                select(PriceSnapshot.exchange, PriceSnapshot.id)
                .where(PriceSnapshot.symbol == symbol)
                .order_by(PriceSnapshot.exchange, PriceSnapshot.fetched_at.desc())
            )
            # SQLite has no DISTINCT ON — pull ordered rows and keep the
            # first (most recent) one per exchange in Python. Simple and
            # fine at this data volume; revisit with a window function
            # (ROW_NUMBER) if this table grows into the millions of rows.
            rows = (await session.execute(
                select(PriceSnapshot).where(PriceSnapshot.symbol == symbol)
                .order_by(PriceSnapshot.fetched_at.desc())
            )).scalars().all()

            seen: set[str] = set()
            latest: List[dict] = []
            for row in rows:
                if row.exchange in seen:
                    continue
                seen.add(row.exchange)
                latest.append(model_to_dict(row))
            return latest

    async def get_history(
        self,
        symbol: str,
        exchange: Optional[str] = None,
        hours: int = 24,
        limit: int = 500,
    ) -> List[dict]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        async with self._session_factory() as session:
            stmt = select(PriceSnapshot).where(
                PriceSnapshot.symbol == symbol, PriceSnapshot.fetched_at >= since
            )
            if exchange:
                stmt = stmt.where(PriceSnapshot.exchange == exchange)
            stmt = stmt.order_by(PriceSnapshot.fetched_at.desc()).limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            return [model_to_dict(r) for r in rows]
