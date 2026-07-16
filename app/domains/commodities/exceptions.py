"""
app/domains/commodities/exceptions.py
========================================
Same pattern as every other domain — subclass `LotusError`.
"""
from __future__ import annotations

from app.core.exceptions import LotusError


class UnknownInstrumentError(LotusError):
    status_code = 404


class UnknownGroupError(LotusError):
    status_code = 400


class TsetmcUnavailableError(LotusError):
    """A single instrument fetch failed (timeout, 5xx, malformed response).
    Same partial-failure philosophy as crypto's ExchangeUnavailableError —
    callers should degrade gracefully, not fail the whole batch."""
    status_code = 502


class AllInstrumentsUnavailableError(LotusError):
    status_code = 503


class GoldPlatformUnavailableError(LotusError):
    """A single retail-price platform failed — timeout, non-200, or (most
    likely given how unverified these response shapes are) a parsing
    mismatch. See clients/platform_base.py."""
    status_code = 502


class AllGoldPlatformsUnavailableError(LotusError):
    status_code = 503
