"""
app/domains/crypto/clients/base.py
=====================================
Every exchange client implements the same tiny interface: given a
canonical symbol, return a `RawPrice`. Each concrete client owns:
  - its own base URL and endpoint path
  - its own raw-response Pydantic model (parses that exchange's actual
    JSON shape — see the module docstring in schemas.py)
  - its own canonical-symbol -> exchange-specific-pair mapping
  - its own error translation (HTTP/timeout/parse errors -> ExchangeUnavailableError)

Adding a fourth exchange means adding one file here and one line in
service.py's client list — nothing else changes.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx

from app.domains.crypto.exceptions import ExchangeUnavailableError, UnsupportedSymbolError
from app.domains.crypto.schemas import RawPrice


class ExchangeClient(ABC):
    name: str

    def __init__(self, http: httpx.AsyncClient, symbol_map: dict[str, str], min_interval_seconds: float = 0.0):
        """
        `symbol_map`: canonical symbol (e.g. "USDT-USD") -> this exchange's
        pair string (e.g. Binance "USDTUSD", Kraken "USDTZUSD"). Configured
        per-exchange in Settings rather than hardcoded, since exact pair
        naming varies by exchange and changes over time — see
        app/core/config.py.

        `min_interval_seconds`: crude self-imposed rate limit — refuses to
        call this exchange again within this many seconds of the last
        call, raising ExchangeUnavailableError instead. Combined with
        response caching in the service layer (see service.py), this is
        enough for a single-instance deployment polling a handful of
        symbols. If you outgrow it (many instances, many symbols), replace
        with a real token-bucket limiter (e.g. `aiolimiter`) shared across
        instances via Redis — see ARCHITECTURE.md.
        """
        self._http = http
        self._symbol_map = symbol_map
        self._min_interval = min_interval_seconds
        self._last_call_monotonic: float = 0.0

    def _pair_for(self, symbol: str) -> str:
        try:
            return self._symbol_map[symbol]
        except KeyError:
            raise UnsupportedSymbolError(
                f"{self.name} has no configured pair mapping for symbol '{symbol}'"
            )

    def _check_rate_limit(self) -> None:
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_call_monotonic
        if elapsed < self._min_interval:
            raise ExchangeUnavailableError(
                f"{self.name}: local rate limit ({self._min_interval}s min interval) — try again shortly"
            )

    def _mark_called(self) -> None:
        self._last_call_monotonic = time.monotonic()

    @abstractmethod
    async def fetch_price(self, symbol: str) -> RawPrice:
        """Fetch and return the current price for `symbol`. Raises
        ExchangeUnavailableError or UnsupportedSymbolError on failure —
        never lets an httpx/parse exception escape uncaught, since callers
        (service.fetch_all) expect only those two exception types."""
        raise NotImplementedError
