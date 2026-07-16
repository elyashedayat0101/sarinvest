"""
app/domains/market/repository.py
=================================
Replaces the `LotusDB`-wrapping version of this file. Every method here
does the same thing `lotus_db.py`'s equivalent method did, translated to
SQLAlchemy 2.0 async — same table shapes (see `app/db/models/market.py`),
same upsert-then-recompute-OHLC flow, same return shapes (list of plain
dicts, matching what the routers/schemas already expect).

Method names (`get_contract_detail`, `get_trades`, `get_intraday`,
`get_ohlc`, `get_alerts_recent`) match what `router.py` in this same
package calls. Two more — `upsert_snapshot`, `log_alerts` — are called
from `tasks.py`'s `PersistTask` and are natively async (no more
`run_in_threadpool`/`asyncio.to_thread` wrapping for these calls, since
the queries themselves are non-blocking now instead of calling into
synchronous `sqlite3` code under the hood).

Each method opens its own short-lived `AsyncSession` and commits
immediately — the same "connection lives only as long as the call"
discipline `lotus_db.py`'s `_conn()` context manager used, ported to
`async_sessionmaker` (see `app/db/session.py`).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.market.models import AlertLog, ContractRecord, DailyOHLC, Snapshot, Trade
from app.db.utils import model_to_dict
from app.domains.market.state import AppState


class MarketRepository:
    def __init__(self, state: AppState, session_factory: async_sessionmaker[AsyncSession]):
        self.state = state
        self._session_factory = session_factory

    # ------------------------------------------------------------------ #
    # In-memory reads (AppState) — unchanged, no DB involved
    # ------------------------------------------------------------------ #
    def snapshot_fund(self, fund_id: str):
        return self.state.snapshot_fund(fund_id)

    def find_contract_by_code(self, code: str):
        return self.state.find_contract_by_code(code)

    def health(self) -> dict:
        return self.state.snapshot_health()

    # ------------------------------------------------------------------ #
    # Persistence — called from tasks.py's PersistTask every poll cycle
    # ------------------------------------------------------------------ #
    async def upsert_snapshot(
        self,
        contracts: list,
        fetch_ts: datetime,
        cycle: int = 0,
        spot_map: Optional[dict] = None,
        iv_map: Optional[dict] = None,
        greeks_map: Optional[dict] = None,
    ) -> None:
        spot_map = spot_map or {}
        iv_map = iv_map or {}
        greeks_map = greeks_map or {}
        now_iso = fetch_ts.isoformat()
        today = fetch_ts.strftime("%Y-%m-%d")

        async with self._session_factory() as session:
            async with session.begin():
                prev_vols = await self._get_prev_volumes(session, today)

                for c in contracts:
                    await self._upsert_contract(session, c, now_iso)

                    spot = spot_map.get(c.expiry_j)
                    iv = iv_map.get(c.code)
                    greeks = greeks_map.get(c.code, {})

                    session.add(Snapshot(
                        code=c.code, fetched_at=now_iso, fetch_date=today,
                        last_price=c.last, settle_price=c.settle, prev_settle=c.prev_settle,
                        chg_pct=c.chg_pct, volume=c.volume, value=c.value,
                        high_price=c.high, low_price=c.low,
                        open_interest=c.oi, delta_oi=c.d_oi, demand=c.demand, supply=c.supply,
                        buy_orders=c.buy_orders, sell_orders=c.sell_orders,
                        implied_vol=iv, delta_greek=greeks.get("delta"), theta_greek=greeks.get("theta"),
                        spot_estimate=spot, raw_json=json.dumps({"code": c.code, "settle": c.settle}),
                    ))

                    prev_vol = prev_vols.get(c.code, 0)
                    vol_delta = (c.volume or 0) - prev_vol
                    if vol_delta > 0 and c.last > 0:
                        session.add(Trade(
                            code=c.code, trade_date=today, detected_at=now_iso,
                            price=c.last, volume_delta=vol_delta, cumulative_vol=c.volume,
                            settle_price=c.settle, spot_estimate=spot, implied_vol=iv,
                            demand=c.demand, supply=c.supply, session_cycle=cycle,
                        ))

                # flush so the snapshot rows just added are visible to the
                # OHLC recompute query below, within the same transaction
                await session.flush()
                await self._update_daily_ohlc(session, contracts, today, spot_map, iv_map)

    async def log_alerts(self, alerts: list, fired_at: datetime) -> None:
        now_iso = fired_at.isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                for tag, col, msg in alerts:
                    level = ("critical" if col in ("red",)
                             else "warn" if col in ("yellow", "magenta", "cyan")
                             else "info")
                    code = msg.split()[0] if msg else None
                    session.add(AlertLog(fired_at=now_iso, code=code, tag=tag, level=level, message=msg))

    # ------------------------------------------------------------------ #
    # Query API — called from router.py
    # ------------------------------------------------------------------ #
    async def get_contract_detail(self, code: str, date_str: str) -> dict:
        summary = await self.get_contract_summary(code)
        trades = await self.get_trades(code, date_str, 500)
        intraday = await self.get_intraday(code, date_str)
        ohlc = await self.get_ohlc(code, limit=60)
        return {"code": code, "date": date_str, "summary": summary,
                "trades": trades, "intraday": intraday, "ohlc": ohlc}

    async def get_contract_summary(self, code: str) -> dict:
        today = date.today().strftime("%Y-%m-%d")
        async with self._session_factory() as session:
            contract = (await session.execute(
                select(ContractRecord).where(ContractRecord.code == code)
            )).scalar_one_or_none()
            if not contract:
                return {}

            latest = (await session.execute(
                select(Snapshot).where(Snapshot.code == code, Snapshot.fetch_date == today)
                .order_by(Snapshot.fetched_at.desc()).limit(1)
            )).scalar_one_or_none()

            ohlc = (await session.execute(
                select(DailyOHLC).where(DailyOHLC.code == code, DailyOHLC.trade_date == today)
            )).scalar_one_or_none()

            cnt, total_vol = (await session.execute(
                select(func.count(Trade.id), func.sum(Trade.volume_delta))
                .where(Trade.code == code, Trade.trade_date == today)
            )).one()

            return {
                "contract": model_to_dict(contract),
                "latest": model_to_dict(latest),
                "ohlc": model_to_dict(ohlc),
                "trade_count_today": cnt or 0,
                "volume_today": total_vol or 0,
            }

    async def get_trades(self, code: str, date_str: Optional[str] = None, limit: int = 200) -> list[dict]:
        date_str = date_str or date.today().strftime("%Y-%m-%d")
        async with self._session_factory() as session:
            rows = (await session.execute(
                select(Trade).where(Trade.code == code, Trade.trade_date == date_str)
                .order_by(Trade.detected_at.desc()).limit(limit)
            )).scalars().all()
            return [model_to_dict(r) for r in rows]

    async def get_intraday(self, code: str, date_str: Optional[str] = None) -> list[dict]:
        date_str = date_str or date.today().strftime("%Y-%m-%d")
        cols = (Snapshot.fetched_at, Snapshot.last_price, Snapshot.settle_price, Snapshot.volume,
                Snapshot.open_interest, Snapshot.implied_vol, Snapshot.demand, Snapshot.supply,
                Snapshot.high_price, Snapshot.low_price, Snapshot.spot_estimate)
        async with self._session_factory() as session:
            result = await session.execute(
                select(*cols).where(Snapshot.code == code, Snapshot.fetch_date == date_str)
                .order_by(Snapshot.fetched_at)
            )
            return [dict(row._mapping) for row in result.all()]

    async def get_ohlc(
        self, code: str, date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = 120
    ) -> list[dict]:
        date_from = date_from or "2020-01-01"
        date_to = date_to or date.today().strftime("%Y-%m-%d")
        async with self._session_factory() as session:
            rows = (await session.execute(
                select(DailyOHLC).where(
                    DailyOHLC.code == code, DailyOHLC.trade_date.between(date_from, date_to)
                ).order_by(DailyOHLC.trade_date.desc()).limit(limit)
            )).scalars().all()
            return [model_to_dict(r) for r in rows]

    async def get_alerts_recent(self, limit: int = 50) -> list[dict]:
        async with self._session_factory() as session:
            rows = (await session.execute(
                select(AlertLog).order_by(AlertLog.fired_at.desc()).limit(limit)
            )).scalars().all()
            return [model_to_dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #
    async def _get_prev_volumes(self, session: AsyncSession, today: str) -> dict:
        subq = (
            select(func.max(Snapshot.id))
            .where(Snapshot.fetch_date == today)
            .group_by(Snapshot.code)
        )
        result = await session.execute(
            select(Snapshot.code, Snapshot.volume).where(
                Snapshot.fetch_date == today, Snapshot.id.in_(subq)
            )
        )
        return {code: (volume or 0) for code, volume in result.all()}

    async def _upsert_contract(self, session: AsyncSession, c, now_iso: str) -> None:
        stmt = sqlite_insert(ContractRecord).values(
            code=c.code, description=c.desc, opt_type=c.opt, strike=c.strike,
            expiry_jalali=c.expiry_j, expiry_gregorian=str(c.expiry_g or ""),
            month_label=c.month, first_seen=now_iso, last_seen=now_iso,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ContractRecord.code],
            set_={
                "last_seen": stmt.excluded.last_seen,
                "description": stmt.excluded.description,
                "expiry_gregorian": stmt.excluded.expiry_gregorian,
                "month_label": stmt.excluded.month_label,
            },
        )
        await session.execute(stmt)

    async def _update_daily_ohlc(
        self, session: AsyncSession, contracts: list, today: str, spot_map: dict, iv_map: dict
    ) -> None:
        for c in contracts:
            rows = (await session.execute(
                select(Snapshot.last_price, Snapshot.high_price, Snapshot.low_price)
                .where(Snapshot.code == c.code, Snapshot.fetch_date == today, Snapshot.last_price > 0)
                .order_by(Snapshot.fetched_at)
            )).all()
            if not rows:
                continue

            prices = [r.last_price for r in rows if r.last_price]
            highs = [r.high_price for r in rows if r.high_price]
            lows = [r.low_price for r in rows if r.low_price and r.low_price > 0]
            iv = iv_map.get(c.code)

            stmt = sqlite_insert(DailyOHLC).values(
                code=c.code, trade_date=today,
                open_price=prices[0] if prices else None,
                high_price=max(highs) if highs else None,
                low_price=min(lows) if lows else None,
                close_price=prices[-1] if prices else None,
                total_volume=c.volume, total_value=c.value,
                settle_price=c.settle, open_interest=c.oi, delta_oi=c.d_oi, avg_iv=iv,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[DailyOHLC.code, DailyOHLC.trade_date],
                set_={
                    "high_price": stmt.excluded.high_price,
                    "low_price": stmt.excluded.low_price,
                    "close_price": stmt.excluded.close_price,
                    "total_volume": stmt.excluded.total_volume,
                    "total_value": stmt.excluded.total_value,
                    "settle_price": stmt.excluded.settle_price,
                    "open_interest": stmt.excluded.open_interest,
                    "delta_oi": stmt.excluded.delta_oi,
                    "avg_iv": stmt.excluded.avg_iv,
                },
            )
            await session.execute(stmt)
