"""
app/db/models/portfolio.py
============================
Ported from `portfolio_db.py`'s `PORTFOLIO_SCHEMA` and
`STRATEGY_SCHEMA_ADDITION`. Every `CHECK(...)` and `NOT NULL` from the
original is preserved exactly — these are the same constraints that
smoke-testing found `portfolio_db.py`'s own `convert_strategy_to_positions`
doesn't pre-validate against (see README_MIGRATION.md); keeping them
enforced at the DB layer here means that's still a real safety net, on
top of (not instead of) the Pydantic `Leg` validator in
`app/schemas/strategy.py`.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import PortfolioBase


class Portfolio(PortfolioBase):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str]
    description: Mapped[Optional[str]]
    created_at: Mapped[str]
    updated_at: Mapped[str]
    is_active: Mapped[int] = mapped_column(default=1)


class Position(PortfolioBase):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint("opt_type IN ('C','P')", name="ck_positions_opt_type"),
        CheckConstraint("direction IN ('long','short')", name="ck_positions_direction"),
        CheckConstraint("status IN ('open','closed','expired')", name="ck_positions_status"),
        Index("idx_pos_portfolio", "portfolio_id", "status"),
        Index("idx_pos_code", "contract_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    contract_code: Mapped[str]
    opt_type: Mapped[str]
    strike: Mapped[int]
    expiry_jalali: Mapped[str]
    expiry_gregorian: Mapped[Optional[str]]
    month_label: Mapped[Optional[str]]
    direction: Mapped[str]
    quantity: Mapped[int]
    premium_paid: Mapped[float]
    total_cost: Mapped[float]
    open_date_gregorian: Mapped[str]
    open_date_jalali: Mapped[str]
    close_date_gregorian: Mapped[Optional[str]]
    close_date_jalali: Mapped[Optional[str]]
    close_price: Mapped[Optional[float]]
    status: Mapped[str] = mapped_column(default="open")
    notes: Mapped[Optional[str]]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class MarketCache(PortfolioBase):
    """Latest market data per contract, refreshed every poll cycle."""
    __tablename__ = "market_cache"

    contract_code: Mapped[str] = mapped_column(primary_key=True)
    last_price: Mapped[Optional[float]]
    settle_price: Mapped[Optional[float]]
    implied_vol: Mapped[Optional[float]]
    delta_greek: Mapped[Optional[float]]
    theta_greek: Mapped[Optional[float]]
    spot_estimate: Mapped[Optional[float]]
    days_remaining: Mapped[Optional[int]]
    updated_at: Mapped[str]


class PositionPnL(PortfolioBase):
    """P&L history snapshots — defined in the original schema; nothing in
    the given source ever inserted into it (no call site writes here), so
    it's ported for schema fidelity but currently unused, same as before."""
    __tablename__ = "position_pnl"
    __table_args__ = (
        Index("idx_pnl_pos", "position_id", "snap_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    snapshot_at: Mapped[str]
    snap_date: Mapped[str]
    current_price: Mapped[Optional[float]]
    settle_price: Mapped[Optional[float]]
    implied_vol: Mapped[Optional[float]]
    spot_estimate: Mapped[Optional[float]]
    delta_greek: Mapped[Optional[float]]
    theta_greek: Mapped[Optional[float]]
    unrealized_pnl: Mapped[Optional[float]]
    pnl_pct: Mapped[Optional[float]]
    days_remaining: Mapped[Optional[int]]
    time_value: Mapped[Optional[float]]
    intrinsic_value: Mapped[Optional[float]]


class PositionNote(PortfolioBase):
    __tablename__ = "position_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    created_at: Mapped[str]
    note: Mapped[str]


class Strategy(PortfolioBase):
    __tablename__ = "strategies"
    __table_args__ = (
        CheckConstraint("status IN ('draft','active','closed')", name="ck_strategies_status"),
        Index("idx_strat_portfolio", "portfolio_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[Optional[int]] = mapped_column(ForeignKey("portfolios.id"))
    name: Mapped[str]
    strategy_type: Mapped[str]
    status: Mapped[str] = mapped_column(default="draft")
    underlying_spot_at_entry: Mapped[Optional[float]]
    entry_date_gregorian: Mapped[Optional[str]]
    entry_date_jalali: Mapped[Optional[str]]
    net_cost: Mapped[Optional[float]]
    max_profit: Mapped[Optional[float]]
    max_loss: Mapped[Optional[float]]
    breakevens_json: Mapped[Optional[str]]
    notes: Mapped[Optional[str]]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class StrategyLeg(PortfolioBase):
    __tablename__ = "strategy_legs"
    __table_args__ = (
        CheckConstraint("leg_type IN ('option','stock')", name="ck_legs_leg_type"),
        CheckConstraint("opt_type IN ('C','P',NULL)", name="ck_legs_opt_type"),
        CheckConstraint("action IN ('buy','sell')", name="ck_legs_action"),
        Index("idx_strat_legs", "strategy_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"))
    leg_index: Mapped[int]
    leg_type: Mapped[str]
    contract_code: Mapped[Optional[str]]
    opt_type: Mapped[Optional[str]]
    action: Mapped[str]
    strike: Mapped[Optional[int]]
    expiry_jalali: Mapped[Optional[str]]
    expiry_gregorian: Mapped[Optional[str]]
    quantity: Mapped[int]
    entry_price: Mapped[float]
    entry_iv: Mapped[Optional[float]]
    linked_position_id: Mapped[Optional[int]] = mapped_column(ForeignKey("positions.id"))
