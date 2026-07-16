"""
app/domains/crypto/clients/coinbase.py
=========================================
Uses Coinbase Exchange's public `/products/{product_id}/ticker` endpoint
(bid/ask/volume included), not the simpler `/v2/prices/.../spot` endpoint
on api.coinbase.com (price only, no bid/ask/volume). Verify against
Coinbase's live docs before depending on this in production — same
caveat as binance.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, ValidationError

from app.domains.crypto.clients.base import ExchangeClient
from app.domains.crypto.exceptions import ExchangeUnavailableError
from app.domains.crypto.schemas import RawPrice


class _CoinbaseTicker(BaseModel):
    price: str
    bid: str
    ask: str
    volume: str


class CoinbaseClient(ExchangeClient):
    name = "coinbase"

    def __init__(self, http: httpx.AsyncClient, symbol_map: dict[str, str], base_url: str, min_interval_seconds: float = 0.0):
        super().__init__(http, symbol_map, min_interval_seconds)
        self._base_url = base_url.rstrip("/")

    async def fetch_price(self, symbol: str) -> RawPrice:
        self._check_rate_limit()
        product_id = self._pair_for(symbol)
        try:
            resp = await self._http.get(f"{self._base_url}/products/{product_id}/ticker")
            resp.raise_for_status()
            parsed = _CoinbaseTicker.model_validate(resp.json())
        except httpx.HTTPError as e:
            raise ExchangeUnavailableError(f"coinbase: request failed for {product_id}: {e}") from e
        except (ValidationError, ValueError) as e:
            raise ExchangeUnavailableError(f"coinbase: unexpected response shape for {product_id}: {e}") from e
        finally:
            self._mark_called()

        return RawPrice(
            exchange=self.name,
            symbol=symbol,
            price=float(parsed.price),
            bid=float(parsed.bid),
            ask=float(parsed.ask),
            volume_24h=float(parsed.volume),
            fetched_at=datetime.now(timezone.utc),
        )
