"""
app/domains/portfolio/repository.py
====================================
Replaces the `PortfolioDB`-wrapping version of this file. Method names
are unchanged from before (`list_portfolios`, `add_position`, `get_summary`,
`save_strategy`, `convert_strategy_to_positions`, etc.), so `router.py`
in this same package needed zero changes to those call sites when this
domain was consolidated out of `app/repositories/`.

One deliberate behavior change from the original `portfolio_db.py`,
called out explicitly rather than left silent: `convert_strategy_to_positions`
originally called `self.add_position(...)` from inside `with self._conn()`,
but `add_position` opens *its own* separate connection — so in the
original code, each created position committed in its own transaction,
independent of the "link leg to position" and "mark strategy active"
updates that followed. If a later leg failed, earlier legs' positions
would already be committed while the strategy was never marked active.
Here, the whole conversion (all positions + all leg links + the status
update) runs inside one `session.begin()` block — genuinely atomic. This
is a behavior improvement, not just a syntax port; flagging it in case
anything downstream relied on the old partial-commit behavior.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func, select, update, delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.portfolio.models import (
    MarketCache, Portfolio, Position, PositionNote, PositionPnL, Strategy, StrategyLeg,
)
from app.db.utils import model_to_dict
from app.domain.jalali import greg_to_jalali


class PortfolioRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    # ------------------------------------------------------------------ #
    # Startup — replaces the "seed a default portfolio" logic that used
    # to run inline in PortfolioDB.__init__ / _init()
    # ------------------------------------------------------------------ #
    async def ensure_default_portfolio(self) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                count = (await session.execute(select(func.count(Portfolio.id)))).scalar_one()
                if count == 0:
                    now = datetime.now().isoformat()
                    session.add(Portfolio(
                        name="پرتفوی اصلی", description="پرتفوی پیش‌فرض",
                        created_at=now, updated_at=now,
                    ))

    # ------------------------------------------------------------------ #
    # Market cache — updated every poll cycle from persist_service.py
    # ------------------------------------------------------------------ #
    async def update_market_cache(self, contracts: list, spot_map: dict, iv_map: dict, greeks_map: dict) -> None:
        now = datetime.now().isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                for c in contracts:
                    days = None
                    if c.expiry_g:
                        days = max(0, (c.expiry_g - date.today()).days)
                    stmt = sqlite_insert(MarketCache).values(
                        contract_code=c.code,
                        last_price=c.last if c.last > 0 else c.settle,
                        settle_price=c.settle,
                        implied_vol=iv_map.get(c.code),
                        delta_greek=greeks_map.get(c.code, {}).get("delta"),
                        theta_greek=greeks_map.get(c.code, {}).get("theta"),
                        spot_estimate=spot_map.get(c.expiry_j),
                        days_remaining=days,
                        updated_at=now,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[MarketCache.contract_code],
                        set_={
                            "last_price": stmt.excluded.last_price,
                            "settle_price": stmt.excluded.settle_price,
                            "implied_vol": stmt.excluded.implied_vol,
                            "delta_greek": stmt.excluded.delta_greek,
                            "theta_greek": stmt.excluded.theta_greek,
                            "spot_estimate": stmt.excluded.spot_estimate,
                            "days_remaining": stmt.excluded.days_remaining,
                            "updated_at": stmt.excluded.updated_at,
                        },
                    )
                    await session.execute(stmt)

    async def get_market_data(self, contract_code: str) -> dict:
        async with self._session_factory() as session:
            row = (await session.execute(
                select(MarketCache).where(MarketCache.contract_code == contract_code)
            )).scalar_one_or_none()
            return model_to_dict(row)

    # ------------------------------------------------------------------ #
    # Portfolio CRUD
    # ------------------------------------------------------------------ #
    async def list_portfolios(self) -> list[dict]:
        async with self._session_factory() as session:
            rows = (await session.execute(
                select(Portfolio).where(Portfolio.is_active == 1).order_by(Portfolio.created_at)
            )).scalars().all()
            return [model_to_dict(r) for r in rows]

    async def create_portfolio(self, name: str, description: str = "") -> int:
        now = datetime.now().isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                obj = Portfolio(name=name, description=description, created_at=now, updated_at=now)
                session.add(obj)
                await session.flush()
                return obj.id

    async def delete_portfolio(self, portfolio_id: int) -> None:
        now = datetime.now().isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Portfolio).where(Portfolio.id == portfolio_id)
                    .values(is_active=0, updated_at=now)
                )

    # ------------------------------------------------------------------ #
    # Position CRUD
    # ------------------------------------------------------------------ #
    def _build_position(
        self, *, portfolio_id, contract_code, opt_type, strike, expiry_jalali,
        expiry_gregorian, month_label, direction, quantity, premium_paid,
        open_date_gregorian=None, notes="",
    ) -> Position:
        now = datetime.now().isoformat()
        open_date_g = open_date_gregorian or date.today().strftime("%Y-%m-%d")
        open_date_j = greg_to_jalali(open_date_g)
        sign = 1 if direction == "long" else -1
        total_cost = quantity * premium_paid * sign
        return Position(
            portfolio_id=portfolio_id, contract_code=contract_code, opt_type=opt_type, strike=strike,
            expiry_jalali=expiry_jalali, expiry_gregorian=expiry_gregorian, month_label=month_label,
            direction=direction, quantity=quantity, premium_paid=premium_paid, total_cost=total_cost,
            open_date_gregorian=open_date_g, open_date_jalali=open_date_j,
            notes=notes, created_at=now, updated_at=now,
        )

    async def add_position(
        self, portfolio_id: int, contract_code: str, opt_type: str, strike: int,
        expiry_jalali: str, expiry_gregorian: str, month_label: str, direction: str,
        quantity: int, premium_paid: float, open_date_gregorian: Optional[str] = None, notes: str = "",
    ) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                obj = self._build_position(
                    portfolio_id=portfolio_id, contract_code=contract_code, opt_type=opt_type,
                    strike=strike, expiry_jalali=expiry_jalali, expiry_gregorian=expiry_gregorian,
                    month_label=month_label, direction=direction, quantity=quantity,
                    premium_paid=premium_paid, open_date_gregorian=open_date_gregorian, notes=notes,
                )
                session.add(obj)
                await session.flush()
                return obj.id

    async def close_position(self, position_id: int, close_price: float, close_date_gregorian: Optional[str] = None) -> None:
        close_date_g = close_date_gregorian or date.today().strftime("%Y-%m-%d")
        close_date_j = greg_to_jalali(close_date_g)
        now = datetime.now().isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Position).where(Position.id == position_id).values(
                        status="closed", close_date_gregorian=close_date_g,
                        close_date_jalali=close_date_j, close_price=close_price, updated_at=now,
                    )
                )

    async def delete_position(self, position_id: int) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(delete(PositionPnL).where(PositionPnL.position_id == position_id))
                await session.execute(delete(PositionNote).where(PositionNote.position_id == position_id))
                await session.execute(delete(Position).where(Position.id == position_id))

    async def get_positions(self, portfolio_id: Optional[int] = None, status: Optional[str] = "open") -> list[dict]:
        async with self._session_factory() as session:
            stmt = select(Position, Portfolio.name.label("portfolio_name")).join(
                Portfolio, Portfolio.id == Position.portfolio_id
            )
            if portfolio_id is not None:
                stmt = stmt.where(Position.portfolio_id == portfolio_id)
            if status:
                stmt = stmt.where(Position.status == status)
            stmt = stmt.order_by(Position.created_at.desc())
            result = await session.execute(stmt)
            out = []
            for position, portfolio_name in result.all():
                d = model_to_dict(position)
                d["portfolio_name"] = portfolio_name
                out.append(d)
            return out

    async def get_position(self, position_id: int) -> Optional[dict]:
        async with self._session_factory() as session:
            row = (await session.execute(
                select(Position).where(Position.id == position_id)
            )).scalar_one_or_none()
            return model_to_dict(row) if row else None

    # ------------------------------------------------------------------ #
    # P&L — pure computation over `pos` dict + live market_cache, no writes
    # ------------------------------------------------------------------ #
    async def compute_position_pnl(self, pos: dict) -> dict:
        mkt = await self.get_market_data(pos["contract_code"])

        current_price = mkt.get("last_price") or mkt.get("settle_price") or 0
        premium = pos["premium_paid"]
        qty = pos["quantity"]
        strike = pos["strike"]
        is_call = pos["opt_type"] == "C"
        direction = pos["direction"]
        spot = mkt.get("spot_estimate") or 0

        if direction == "long":
            price_diff = current_price - premium
        else:
            price_diff = premium - current_price

        unrealized_pnl = price_diff * qty
        pnl_pct = (price_diff / premium * 100) if premium > 0 else 0

        if spot > 0:
            intrinsic = max(0, (spot - strike) if is_call else (strike - spot))
        else:
            intrinsic = 0
        time_value = max(0, current_price - intrinsic)

        days_remaining = mkt.get("days_remaining")
        if days_remaining is None:
            exp_g = pos.get("expiry_gregorian", "")
            if exp_g:
                try:
                    parts = exp_g.split("-")
                    exp = date(int(parts[0]), int(parts[1]), int(parts[2]))
                    days_remaining = max(0, (exp - date.today()).days)
                except Exception:
                    days_remaining = None

        theta = mkt.get("theta_greek") or 0
        if direction == "short":
            theta = -theta

        return {
            "current_price": current_price,
            "settle_price": mkt.get("settle_price", 0),
            "premium_paid": premium,
            "price_diff": price_diff,
            "unrealized_pnl": unrealized_pnl,
            "pnl_pct": pnl_pct,
            "intrinsic_value": intrinsic,
            "time_value": time_value,
            "implied_vol": mkt.get("implied_vol"),
            "delta_greek": mkt.get("delta_greek"),
            "theta_greek": theta,
            "spot_estimate": spot,
            "days_remaining": days_remaining,
            "has_market_data": bool(current_price > 0),
            "market_updated": mkt.get("updated_at", ""),
        }

    def compute_closed_pnl(self, pos: dict) -> dict:
        premium = pos["premium_paid"]
        close_price = pos.get("close_price") or 0
        qty = pos["quantity"]
        direction = pos["direction"]

        price_diff = (close_price - premium) if direction == "long" else (premium - close_price)
        realized_pnl = price_diff * qty
        pnl_pct = (price_diff / premium * 100) if premium > 0 else 0

        return {
            "premium_paid": premium, "close_price": close_price, "price_diff": price_diff,
            "realized_pnl": realized_pnl, "pnl_pct": pnl_pct,
        }

    async def get_summary(self, portfolio_id: int) -> dict:
        open_pos = await self.get_positions(portfolio_id, "open")
        closed_pos = await self.get_positions(portfolio_id, "closed")

        total_invested = 0.0
        total_unrealized = 0.0
        open_enriched = []
        for p in open_pos:
            pnl = await self.compute_position_pnl(p)
            total_invested += p["premium_paid"] * p["quantity"]
            total_unrealized += pnl["unrealized_pnl"]
            open_enriched.append({**p, "pnl": pnl})

        total_realized = 0.0
        closed_enriched = []
        for p in closed_pos:
            cpnl = self.compute_closed_pnl(p)
            total_realized += cpnl["realized_pnl"]
            closed_enriched.append({**p, "pnl": cpnl})

        total_pnl = total_unrealized + total_realized
        pnl_pct = (total_unrealized / total_invested * 100) if total_invested else 0

        return {
            "portfolio_id": portfolio_id,
            "open_count": len(open_pos),
            "closed_count": len(closed_pos),
            "total_invested": total_invested,
            "unrealized_pnl": total_unrealized,
            "realized_pnl": total_realized,
            "total_pnl": total_pnl,
            "pnl_pct": pnl_pct,
            "positions": open_enriched,
            "closed": closed_enriched,
        }

    async def add_note(self, position_id: int, note: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                session.add(PositionNote(
                    position_id=position_id, created_at=datetime.now().isoformat(), note=note
                ))

    async def get_notes(self, position_id: int) -> list[dict]:
        async with self._session_factory() as session:
            rows = (await session.execute(
                select(PositionNote).where(PositionNote.position_id == position_id)
                .order_by(PositionNote.created_at.desc())
            )).scalars().all()
            return [model_to_dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Strategies
    # ------------------------------------------------------------------ #
    async def save_strategy(
        self, portfolio_id: int, name: str, strategy_type: str, legs: list,
        underlying_spot_at_entry: float, analysis: Optional[dict] = None, notes: str = "",
    ) -> int:
        now = datetime.now().isoformat()
        today_g = date.today().strftime("%Y-%m-%d")
        today_j = greg_to_jalali(today_g)
        analysis = analysis or {}

        async with self._session_factory() as session:
            async with session.begin():
                strat = Strategy(
                    portfolio_id=portfolio_id, name=name, strategy_type=strategy_type, status="draft",
                    underlying_spot_at_entry=underlying_spot_at_entry,
                    entry_date_gregorian=today_g, entry_date_jalali=today_j,
                    net_cost=analysis.get("net_cost"), max_profit=analysis.get("max_profit"),
                    max_loss=analysis.get("max_loss"),
                    breakevens_json=json.dumps(analysis.get("breakevens", [])),
                    notes=notes, created_at=now, updated_at=now,
                )
                session.add(strat)
                await session.flush()  # populate strat.id for the legs below

                for i, leg in enumerate(legs):
                    session.add(StrategyLeg(
                        strategy_id=strat.id, leg_index=i, leg_type=leg["leg_type"],
                        contract_code=leg.get("contract_code"), opt_type=leg.get("opt_type"),
                        action=leg["action"], strike=leg.get("strike"),
                        expiry_jalali=leg.get("expiry_jalali"), expiry_gregorian=leg.get("expiry_gregorian"),
                        quantity=leg["quantity"], entry_price=leg["entry_price"], entry_iv=leg.get("entry_iv"),
                    ))
                return strat.id

    async def list_strategies(self, portfolio_id: Optional[int] = None, status: Optional[str] = None) -> list[dict]:
        async with self._session_factory() as session:
            stmt = select(Strategy)
            if portfolio_id is not None:
                stmt = stmt.where(Strategy.portfolio_id == portfolio_id)
            if status:
                stmt = stmt.where(Strategy.status == status)
            stmt = stmt.order_by(Strategy.created_at.desc())
            rows = (await session.execute(stmt)).scalars().all()
            return [model_to_dict(r) for r in rows]

    async def get_strategy(self, strategy_id: int) -> Optional[dict]:
        async with self._session_factory() as session:
            strat = (await session.execute(
                select(Strategy).where(Strategy.id == strategy_id)
            )).scalar_one_or_none()
            if not strat:
                return None
            legs = (await session.execute(
                select(StrategyLeg).where(StrategyLeg.strategy_id == strategy_id)
                .order_by(StrategyLeg.leg_index)
            )).scalars().all()
            d = model_to_dict(strat)
            d["legs"] = [model_to_dict(leg) for leg in legs]
            d["breakevens"] = json.loads(d.get("breakevens_json") or "[]")
            return d

    async def delete_strategy(self, strategy_id: int) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(delete(StrategyLeg).where(StrategyLeg.strategy_id == strategy_id))
                await session.execute(delete(Strategy).where(Strategy.id == strategy_id))

    async def convert_strategy_to_positions(self, strategy_id: int) -> list[int]:
        """Turn option legs into real positions. Stock legs are skipped
        (this app tracks option positions only), same as the original.
        Runs as one atomic transaction — see the module docstring for how
        this differs from (and improves on) the original's behavior."""
        strat = await self.get_strategy(strategy_id)
        if not strat:
            return []

        created_ids: list[int] = []
        async with self._session_factory() as session:
            async with session.begin():
                for leg in strat["legs"]:
                    if leg["leg_type"] != "option":
                        continue
                    direction = "long" if leg["action"] == "buy" else "short"
                    pos = self._build_position(
                        portfolio_id=strat["portfolio_id"], contract_code=leg["contract_code"],
                        opt_type=leg["opt_type"], strike=leg["strike"],
                        expiry_jalali=leg["expiry_jalali"], expiry_gregorian=leg["expiry_gregorian"],
                        month_label="", direction=direction, quantity=leg["quantity"],
                        premium_paid=leg["entry_price"],
                        notes=f"از استراتژی «{strat['name']}» (#{strategy_id})",
                    )
                    session.add(pos)
                    await session.flush()  # populate pos.id
                    await session.execute(
                        update(StrategyLeg).where(StrategyLeg.id == leg["id"])
                        .values(linked_position_id=pos.id)
                    )
                    created_ids.append(pos.id)

                await session.execute(
                    update(Strategy).where(Strategy.id == strategy_id)
                    .values(status="active", updated_at=datetime.now().isoformat())
                )
        return created_ids
