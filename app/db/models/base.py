"""
app/db/models/base.py
=======================
Two separate `DeclarativeBase` classes, matching the original design of
two separate SQLite files (`lotus_options.db` for market data,
`lotus_portfolio.db` for portfolios/positions/strategies). This migration
replaces *how* those databases are accessed (SQLAlchemy instead of raw
`sqlite3`), not the fact that there are two of them — merging them wasn't
asked for and would be a bigger behavioral change than "use an ORM."
"""
from sqlalchemy.orm import DeclarativeBase


class MarketBase(DeclarativeBase):
    """Metadata root for contracts/snapshots/trades/daily_ohlc/alerts_log."""
    pass


class PortfolioBase(DeclarativeBase):
    """Metadata root for portfolios/positions/market_cache/strategies/etc."""
    pass


class SharedBase(DeclarativeBase):
    """
    Metadata root for every domain added from here on (crypto first, then
    users/payments/weather/analytics/...). One shared database instead of
    one-SQLite-file-per-domain — see ARCHITECTURE.md. `market`/`portfolio`
    are NOT moved onto this base yet; that's a deliberate, separate,
    low-risk migration rather than something bundled into an unrelated
    feature change.
    """
    pass
