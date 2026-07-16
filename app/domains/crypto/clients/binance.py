"""
app/domains/crypto/clients/binance.py
========================================
Uses Binance's `/api/v3/ticker/24hr` endpoint (not the plain `ticker/price`
one) because it also returns bid/ask/volume, which the unified `RawPrice`
wants — `ticker/price` only returns the last trade price.

Endpoint/field names are current as of this codebase's writing but
exchange APIs do change; verify against Binance's live docs before
depending on this in production. Base URL is configurable
(`settings.crypto_binance_base_url`) specifically so this can point at
Binance's testnet or be swapped without a code change.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, ValidationError

from app.domains.crypto.clients.base import ExchangeClient
from app.domains.crypto.exceptions import ExchangeUnavailableError
from app.domains.crypto.schemas import RawPrice


class _BinanceTicker24hr(BaseModel):
    """Parses Binance's actual JSON shape. Only the fields we use are
    declared — Pydantic ignores the rest by default, so this doesn't break
    if Binance adds fields."""
    symbol: str
    lastPrice: str
    bidPrice: str
    askPrice: str
    volume: str


class BinanceClient(ExchangeClient):
    name = "binance"

    def __init__(self, http: httpx.AsyncClient, symbol_map: dict[str, str], base_url: str, min_interval_seconds: float = 0.0):
        super().__init__(http, symbol_map, min_interval_seconds)
        self._base_url = base_url.rstrip("/")

    async def fetch_price(self, symbol: str) -> RawPrice:
        self._check_rate_limit()
        pair = self._pair_for(symbol)
        try:
            resp = await self._http.get(
                f"{self._base_url}/api/v3/ticker/24hr", params={"symbol": pair}
            )
            resp.raise_for_status()
            parsed = _BinanceTicker24hr.model_validate(resp.json())
        except httpx.HTTPError as e:
            raise ExchangeUnavailableError(f"binance: request failed for {pair}: {e}") from e
        except (ValidationError, ValueError) as e:
            raise ExchangeUnavailableError(f"binance: unexpected response shape for {pair}: {e}") from e
        finally:
            self._mark_called()

        return RawPrice(
            exchange=self.name,
            symbol=symbol,
            price=float(parsed.lastPrice),
            bid=float(parsed.bidPrice),
            ask=float(parsed.askPrice),
            volume_24h=float(parsed.volume),
            fetched_at=datetime.now(timezone.utc),
        )
