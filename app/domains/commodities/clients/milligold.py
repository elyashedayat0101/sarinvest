"""
app/domains/commodities/clients/milligold.py
==================================================
UNVERIFIED — see clients/platform_base.py's module docstring.

`milli.gold/api/v1/public/milli-price/external` — returns a single
price (price18) without separate buy/sell. Use the same price for
both buy and sell since no spread is provided.
Response format:
{
  "code": 0,
  "message": "عملیات با موفقیت انجام شد.",
  "data": {
    "price18": 182400,
    "date": "2026-07-16T15:44:31"
  }
}
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient
from app.domains.commodities.exceptions import GoldPlatformUnavailableError
from app.domains.commodities.schemas import RawGoldPlatformPrice


class MilliGoldClient(GoldPricePlatformClient):
    name = "milligold"
    _URL = "https://milli.gold/api/v1/public/milli-price/external"
    _HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def fetch_price(self) -> RawGoldPlatformPrice:
        try:
            resp = await self._http.get(self._URL, headers=self._HEADERS)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as e:
            raise GoldPlatformUnavailableError(f"{self.name}: request failed: {e}") from e

        # Check success code
        if payload.get("code") != 0:
            raise GoldPlatformUnavailableError(
                f"{self.name}: API returned error code {payload.get('code')}: {payload.get('message')}"
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise GoldPlatformUnavailableError(
                f"{self.name}: missing 'data' object in response: {payload}"
            )

        price18 = data.get("price18")
        if price18 is None:
            raise GoldPlatformUnavailableError(
                f"{self.name}: missing 'price18' in data: {data}"
            )

        # Use the same price for both buy and sell since no spread is provided
        price = float(price18)

        return RawGoldPlatformPrice(
            platform=self.name,
            buy_price=price,
            sell_price=price,
            fetched_at=datetime.now(timezone.utc),
        )