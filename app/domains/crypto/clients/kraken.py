"""
app/domains/crypto/clients/kraken.py
=======================================
Kraken's Ticker endpoint is the odd one out in two ways, both handled
here so nothing downstream needs to know about them:

1. Response shape is `{"error": [...], "result": {"<PAIR>": {...}}}` —
   errors come back as HTTP 200 with a populated `error` array, not as an
   HTTP error status, so `resp.raise_for_status()` alone won't catch them.
2. The key Kraken uses in `result` for a given pair doesn't always match
   the pair string you requested (e.g. altname vs wsname differences) —
   this client reads whatever single key comes back rather than assuming
   it equals the requested pair.

Field meanings: `c` = last trade closed [price, lot volume], `b` = best
bid [price, ...], `a` = best ask [price, ...], `v` = volume [today,
last 24h] — index 1 is the rolling 24h figure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

import httpx
from pydantic import BaseModel, ValidationError

from app.domains.crypto.clients.base import ExchangeClient
from app.domains.crypto.exceptions import ExchangeUnavailableError
from app.domains.crypto.schemas import RawPrice


class _KrakenPairTicker(BaseModel):
    a: List[str]  # ask [price, whole lot volume, lot volume]
    b: List[str]  # bid [price, whole lot volume, lot volume]
    c: List[str]  # last trade closed [price, lot volume]
    v: List[str]  # volume [today, last 24 hours]


class _KrakenResponse(BaseModel):
    error: List[str]
    result: Dict[str, _KrakenPairTicker]


class KrakenClient(ExchangeClient):
    name = "kraken"

    def __init__(self, http: httpx.AsyncClient, symbol_map: dict[str, str], base_url: str, min_interval_seconds: float = 0.0):
        super().__init__(http, symbol_map, min_interval_seconds)
        self._base_url = base_url.rstrip("/")

    async def fetch_price(self, symbol: str) -> RawPrice:
        self._check_rate_limit()
        pair = self._pair_for(symbol)
        try:
            resp = await self._http.get(f"{self._base_url}/0/public/Ticker", params={"pair": pair})
            resp.raise_for_status()
            parsed = _KrakenResponse.model_validate(resp.json())
        except httpx.HTTPError as e:
            raise ExchangeUnavailableError(f"kraken: request failed for {pair}: {e}") from e
        except (ValidationError, ValueError) as e:
            raise ExchangeUnavailableError(f"kraken: unexpected response shape for {pair}: {e}") from e
        finally:
            self._mark_called()

        if parsed.error:
            raise ExchangeUnavailableError(f"kraken: API error for {pair}: {', '.join(parsed.error)}")
        if not parsed.result:
            raise ExchangeUnavailableError(f"kraken: empty result for {pair}")

        ticker = next(iter(parsed.result.values()))  # see module docstring: key may not equal `pair`

        return RawPrice(
            exchange=self.name,
            symbol=symbol,
            price=float(ticker.c[0]),
            bid=float(ticker.b[0]),
            ask=float(ticker.a[0]),
            volume_24h=float(ticker.v[1]),
            fetched_at=datetime.now(timezone.utc),
        )
