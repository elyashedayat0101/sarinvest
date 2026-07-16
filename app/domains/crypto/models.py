"""
app/domains/crypto/models.py
==============================
Registered on `SharedBase` (app/db/models/base.py), not a new per-domain
database file. See ARCHITECTURE.md for why: one SQLite file per domain
doesn't scale past a couple of domains — no cross-domain joins, N
connection pools, N settings to manage. Every new domain from here on
(crypto, then users/payments/weather/analytics) shares one database.
`market`/`portfolio` keep their existing separate files for now; moving
them is a separate, low-risk follow-up (see ARCHITECTURE.md's migration
plan), not bundled into this change.

One table for v1: raw per-exchange observations. Deliberately NOT adding
a separate daily-rollup table yet — `get_history` below can serve
reasonably-ranged queries directly from `crypto_price_snapshots`, and a
rollup table is easy to add later once real query-volume data says you
need it. Adding it now would be optimizing for a load pattern that
doesn't exist yet.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import SharedBase


class PriceSnapshot(SharedBase):
    """One observation of one symbol on one exchange at one point in time."""
    __tablename__ = "crypto_price_snapshots"
    __table_args__ = (
        Index("idx_crypto_symbol_exchange_time", "symbol", "exchange", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    exchange: Mapped[str]                  # "binance" | "coinbase" | "kraken" | ...
    symbol: Mapped[str]                    # canonical form, e.g. "USDT-USD"
    price: Mapped[float]
    bid: Mapped[Optional[float]]
    ask: Mapped[Optional[float]]
    volume_24h: Mapped[Optional[float]]
    fetched_at: Mapped[str]                # ISO datetime string, consistent with the rest of the app
    raw_json: Mapped[Optional[str]]        # audit trail — same pattern as lotus_db's snapshots.raw_json
