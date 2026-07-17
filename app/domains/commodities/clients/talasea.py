"""
app/domains/commodities/clients/talasea.py
================================================
UNVERIFIED — see clients/platform_base.py's module docstring.

`api.talasea.ir/api/market/getGoldPrice` — returns a single price
without separate buy/sell. Use the same price for both.
Response format:
{
  "price": "18240",
  "minOrderValue": 100000,
  "minSellOrderValue": 100000,
  "feeTable": [{"min": 0, "fee": 0.01}],
  "totalOrder30dayValues": 0,
  "minDeposit": 5000,
  "maxDeposit": 400000000,
  "maxOrderValue": 1000000000,
  "fee": 0.01,
  "percentageCreditLoan": 40,
  "goldInstallmentPercent": 0.055,
  "change24h": "0.02",
  "disableBuyMessage": "",
  "disableSellMessage": "",
  "disableSell": false,
  "disableBuy": false,
  "disableMargin": false,
  "disableMarginMessage": "",
  "disableLimit": false,
  "disableLimitMessage": ""
}
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient
from app.domains.commodities.exceptions import GoldPlatformUnavailableError
from app.domains.commodities.schemas import RawGoldPlatformPrice


class TalaseaClient(GoldPricePlatformClient):
    name = "talasea"
    _URL = "https://api.talasea.ir/api/market/getGoldPrice"
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

        # Direct access to price - simple flat structure
        price = payload.get("price")
        if price is None:
            raise GoldPlatformUnavailableError(
                f"{self.name}: missing 'price' in response: {payload}"
            )

        # Use the same price for both buy and sell since no spread is provided
        price_val = float(price)

        return RawGoldPlatformPrice(
            platform=self.name,
            buy_price=price_val,
            sell_price=price_val,
            fetched_at=datetime.now(timezone.utc),
        )

