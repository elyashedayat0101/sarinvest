"""
app/db/models/market.py
=========================
Ported column-for-column, constraint-for-constraint, index-for-index from
`lotus_db.py`'s `SCHEMA` string. `CheckConstraint` text is copied verbatim
from the original `CHECK(...)` clauses rather than re-expressed as Python
`Enum` columns — that keeps the on-disk schema byte-identical to before
(safe if you're migrating an existing `lotus_options.db` file rather than
starting fresh) and avoids introducing an enum layer that `lotus_monitor.py`
/ `strategy_engine.py` (still not available) may not expect.

Named `ContractRecord` rather than `Contract` deliberately — `Contract` is
already the name of the *live, unpersisted* market-data object from
`lotus_monitor.py`. Two different classes named `Contract` in different
modules would work but reads badly; `ContractRecord` makes clear this one
is a row in the `contracts` table.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import MarketBase


class ContractRecord(MarketBase):
    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint("opt_type IN ('C','P')", name="ck_contracts_opt_type"),
    )

    code: Mapped[str] = mapped_column(primary_key=True)
    description: Mapped[Optional[str]]
    opt_type: Mapped[Optional[str]]
    strike: Mapped[Optional[int]]
    expiry_jalali: Mapped[Optional[str]]
    expiry_gregorian: Mapped[Optional[str]]
    month_label: Mapped[Optional[str]]
    first_seen: Mapped[str]
    last_seen: Mapped[str]


class Snapshot(MarketBase):
    """One row per contract per poll cycle."""
    __tablename__ = "snapshots"
    __table_args__ = (
        Index("idx_snapshots_code_date", "code", "fetch_date"),
        Index("idx_snapshots_fetched", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(ForeignKey("contracts.code"))
    fetched_at: Mapped[str]
    fetch_date: Mapped[str]
    last_price: Mapped[Optional[float]]
    settle_price: Mapped[Optional[float]]
    prev_settle: Mapped[Optional[float]]
    chg_pct: Mapped[Optional[float]]
    volume: Mapped[Optional[int]]
    value: Mapped[Optional[float]]
    high_price: Mapped[Optional[float]]
    low_price: Mapped[Optional[float]]
    open_interest: Mapped[Optional[int]]
    delta_oi: Mapped[Optional[int]]
    demand: Mapped[Optional[int]]
    supply: Mapped[Optional[int]]
    buy_orders: Mapped[Optional[int]]
    sell_orders: Mapped[Optional[int]]
    implied_vol: Mapped[Optional[float]]
    delta_greek: Mapped[Optional[float]]
    theta_greek: Mapped[Optional[float]]
    spot_estimate: Mapped[Optional[float]]
    raw_json: Mapped[Optional[str]]


class Trade(MarketBase):
    """Individual trade inferred from a volume increase between snapshots."""
    __tablename__ = "trades"
    __table_args__ = (
        Index("idx_trades_code_date", "code", "trade_date"),
        Index("idx_trades_detected", "detected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(ForeignKey("contracts.code"))
    trade_date: Mapped[str]
    detected_at: Mapped[str]
    price: Mapped[float]
    volume_delta: Mapped[int]
    cumulative_vol: Mapped[int]
    settle_price: Mapped[Optional[float]]
    spot_estimate: Mapped[Optional[float]]
    implied_vol: Mapped[Optional[float]]
    demand: Mapped[Optional[int]]
    supply: Mapped[Optional[int]]
    session_cycle: Mapped[Optional[int]]


class DailyOHLC(MarketBase):
    """Aggregated daily OHLC, one row per (code, trade_date)."""
    __tablename__ = "daily_ohlc"

    code: Mapped[str] = mapped_column(ForeignKey("contracts.code"), primary_key=True)
    trade_date: Mapped[str] = mapped_column(primary_key=True)
    open_price: Mapped[Optional[float]]
    high_price: Mapped[Optional[float]]
    low_price: Mapped[Optional[float]]
    close_price: Mapped[Optional[float]]
    total_volume: Mapped[Optional[int]]
    total_value: Mapped[Optional[float]]
    settle_price: Mapped[Optional[float]]
    open_interest: Mapped[Optional[int]]
    delta_oi: Mapped[Optional[int]]
    avg_iv: Mapped[Optional[float]]


class AlertLog(MarketBase):
    __tablename__ = "alerts_log"
    __table_args__ = (
        Index("idx_alerts_fired", "fired_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fired_at: Mapped[str]
    code: Mapped[Optional[str]]
    tag: Mapped[str]
    level: Mapped[str]  # 'info' | 'warn' | 'critical'
    message: Mapped[str]
    acknowledged: Mapped[int] = mapped_column(default=0)
