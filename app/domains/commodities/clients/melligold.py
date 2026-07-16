"""
app/domains/commodities/clients/melligold.py
==================================================
UNVERIFIED — see clients/platform_base.py's module docstring.

`melligold.com/api/v1/exchange/buy-sell-price/?symbol=XAU18&format=json`
returns buy/sell prices explicitly.
Response format:
{
  "message": "Success",
  "data": {
    "price_buy": 18257891,
    "price_sell": 18257891,
    "difference_price_buy": 160,
    "difference_price_sell": 160,
    "lower_amounts": {...},
    "system_balance_amount": 10000.0,
    "timestamp": 1784204280
  }
}
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient
from app.domains.commodities.exceptions import GoldPlatformUnavailableError
from app.domains.commodities.schemas import RawGoldPlatformPrice


class MelliGoldClient(GoldPricePlatformClient):
    name = "melligold"
    _URL = "https://melligold.com/api/v1/exchange/buy-sell-price/"
    _HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def __init__(self, http: httpx.AsyncClient, symbol: str = "XAU18"):
        self._http = http
        self._symbol = symbol

    async def fetch_price(self) -> RawGoldPlatformPrice:
        try:
            resp = await self._http.get(
                self._URL, params={"symbol": self._symbol, "format": "json"}, headers=self._HEADERS
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as e:
            raise GoldPlatformUnavailableError(f"{self.name}: request failed: {e}") from e

        # Check message indicates success
        if payload.get("message") != "Success":
            raise GoldPlatformUnavailableError(
                f"{self.name}: API returned non-success message: {payload.get('message')}"
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise GoldPlatformUnavailableError(
                f"{self.name}: missing 'data' object in response: {payload}"
            )

        price_buy = data.get("price_buy")
        price_sell = data.get("price_sell")

        if price_buy is None or price_sell is None:
            raise GoldPlatformUnavailableError(
                f"{self.name}: missing price_buy or price_sell in data: {data}"
            )

        return RawGoldPlatformPrice(
            platform=self.name,
            buy_price=float(price_buy),
            sell_price=float(price_sell),
            fetched_at=datetime.now(timezone.utc),
        )
