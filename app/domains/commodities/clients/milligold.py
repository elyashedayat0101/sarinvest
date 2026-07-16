"""
app/domains/commodities/clients/milligold.py
==================================================
UNVERIFIED — see clients/platform_base.py's module docstring.

`milli.gold/api/v1/public/milli-price/external` — returns a single
price (price18) without separate buy/sell. Since the platform service
excludes failed/partial responses from final results, this client raises
an error when buy/sell aren't both available.
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

        # MilliGold only returns a single price (price18), not separate buy/sell
        # Per requirements, if we can't get both buy and sell, exclude from results
        price18 = data.get("price18")
        if price18 is None:
            raise GoldPlatformUnavailableError(
                f"{self.name}: missing 'price18' in data: {data}"
            )

        # Since only one price is available, we can't provide meaningful buy/sell spread
        # Raise to exclude from aggregated results (partial-failure handling)
        raise GoldPlatformUnavailableError(
            f"{self.name}: only single price (price18={price18}) available, no buy/sell spread"
        )
