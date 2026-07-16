"""
app/domains/crypto/aggregator.py
===================================
Pure functions only — no I/O, no DB, no HTTP — same philosophy as
app/domain/options_math.py and app/domain/alerts.py. This is the layer
that answers "given prices from N exchanges, what's the unified view":
average, median, spread. Kept separate from service.py so it can be unit
tested with plain lists of RawPrice, no mocking required.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import List

from app.domains.crypto.schemas import RawPrice


@dataclass(frozen=True)
class AggregateStats:
    average_price: float
    median_price: float
    min_price: float
    max_price: float
    spread_pct: float  # (max - min) / min * 100 — a quick cross-exchange divergence signal


def aggregate(prices: List[RawPrice]) -> AggregateStats:
    if not prices:
        raise ValueError("aggregate() requires at least one price")

    values = [p.price for p in prices]
    lo, hi = min(values), max(values)
    spread_pct = ((hi - lo) / lo * 100) if lo > 0 else 0.0

    return AggregateStats(
        average_price=round(statistics.mean(values), 8),
        median_price=round(statistics.median(values), 8),
        min_price=lo,
        max_price=hi,
        spread_pct=round(spread_pct, 4),
    )
