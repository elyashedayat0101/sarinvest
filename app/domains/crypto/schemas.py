"""
app/domains/crypto/schemas.py
================================
Three tiers, deliberately kept separate:

1. **Exchange-specific raw models** (`BinanceTicker`, `CoinbaseSpot`,
   `KrakenTicker`) — parse each exchange's actual JSON response shape.
   These live next to their client in `clients/*.py`, not here, because
   they're an implementation detail of talking to that one exchange and
   nothing outside that client file should ever construct or depend on
   them. If Binance changes their response shape, only `clients/binance.py`
   needs to change.

2. **`RawPrice`** (below) — the internal, exchange-agnostic shape every
   client normalizes into. Not exposed via the API directly; it's what
   flows between clients -> aggregator -> repository.

3. **API response models** (below) — what routers actually return.
   `ExchangePriceOut` is `RawPrice` reshaped for the wire (ISO datetime
   instead of whatever internal repr, etc). `UnifiedPriceOut` and
   `PriceComparisonOut` are aggregates across exchanges.

Rule of thumb: never let an exchange's raw JSON shape leak past its own
client file, and never let an internal dataclass leak past the router —
each boundary gets its own model, even when they look similar today.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class RawPrice(BaseModel):
    """Exchange-agnostic price observation — the client layer's output type."""
    model_config = ConfigDict(frozen=True)

    exchange: str
    symbol: str
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume_24h: Optional[float] = None
    fetched_at: datetime


# ---- API response models -------------------------------------------------

class ExchangePriceOut(BaseModel):
    exchange: str
    symbol: str
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume_24h: Optional[float] = None
    fetched_at: str


class ExchangeErrorOut(BaseModel):
    exchange: str
    error: str


class UnifiedPriceOut(BaseModel):
    symbol: str
    average_price: float
    median_price: float
    min_price: float
    max_price: float
    spread_pct: float
    sources: List[ExchangePriceOut]
    errors: List[ExchangeErrorOut] = []  # exchanges that failed this round — partial results, not a 500
    fetched_at: str
    from_cache: bool = False


class PriceComparisonOut(UnifiedPriceOut):
    """Same shape as UnifiedPriceOut today — kept as a distinct model since
    the two endpoints answer different questions ("what's the price" vs
    "how do exchanges compare") and are likely to diverge in fields later
    (e.g. comparison might grow arbitrage-opportunity flags)."""
    pass


class PriceHistoryPoint(BaseModel):
    exchange: str
    price: float
    fetched_at: str


class PriceHistoryOut(BaseModel):
    symbol: str
    exchange: Optional[str] = None  # None = all exchanges
    points: List[PriceHistoryPoint]


class SupportedExchangeOut(BaseModel):
    name: str
    tracked_symbols: List[str]
    healthy: bool
    last_error: Optional[str] = None
