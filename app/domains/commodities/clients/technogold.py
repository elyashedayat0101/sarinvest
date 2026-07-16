"""
app/domains/commodities/clients/technogold.py
==================================================
UNVERIFIED — see clients/platform_base.py's module docstring.

`api2.technogold.gold/customer/tradeables/only-price/1` —
returns a single current gold price with buy/sell fields.
Response format:
{
  "succeed": true,
  "message": "درخواست شما با موفقیت انجام شد.",
  "results": {
    "sell_price": 18146870,
    "buy_price": 18376425
  }
}
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient
from app.domains.commodities.exceptions import GoldPlatformUnavailableError
from app.domains.commodities.schemas import RawGoldPlatformPrice


class TechnoGoldClient(GoldPricePlatformClient):
    name = "technogold"
    _URL = "https://api2.technogold.gold/customer/tradeables/only-price/1"
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

        if not payload.get("succeed"):
            raise GoldPlatformUnavailableError(
                f"{self.name}: API returned error: {payload.get('message', 'unknown')}"
            )

        results = payload.get("results")
        if not isinstance(results, dict):
            raise GoldPlatformUnavailableError(
                f"{self.name}: unexpected response shape (missing 'results' dict): {payload}"
            )

        buy_price = results.get("buy_price")
        sell_price = results.get("sell_price")

        if buy_price is None or sell_price is None:
            raise GoldPlatformUnavailableError(
                f"{self.name}: missing buy_price or sell_price in results: {results}"
            )

        return RawGoldPlatformPrice(
            platform=self.name,
            buy_price=float(buy_price),
            sell_price=float(sell_price),
            fetched_at=datetime.now(timezone.utc),
        )
