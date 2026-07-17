"""
app/domains/commodities/clients/digikala.py
================================================
UNVERIFIED — see clients/platform_base.py's module docstring.
`api.digikala.com/non-inventory/v1/prices/` — returns a simple flat
object with gold18 and silver999 prices. No buy/sell spread provided.
Response format:
{
  "gold18": {"price": 182774, "ttl": 60},
  "silver999": {"price": 3802, "ttl": 60}
}
"""
from __future__ import annotations
from datetime import datetime, timezone
import httpx
from app.domains.commodities.clients.platform_base import GoldPricePlatformClient
from app.domains.commodities.exceptions import GoldPlatformUnavailableError
from app.domains.commodities.schemas import RawGoldPlatformPrice
class DigikalaClient(GoldPricePlatformClient):
    name = "digikala"
    _URL = "https://api.digikala.com/non-inventory/v1/prices/"
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
        # Direct access to gold18 price - simple flat structure
        gold18 = payload.get("gold18")
        if not isinstance(gold18, dict):
            raise GoldPlatformUnavailableError(
                f"{self.name}: missing 'gold18' object in response: {payload}"
            )
        price = gold18.get("price")
        if price is None:
            raise GoldPlatformUnavailableError(
                f"{self.name}: missing 'price' in gold18: {gold18}"
            )
        # Use the same price for both buy and sell since no spread is provided
        price_val = float(price)
        return RawGoldPlatformPrice(
            platform=self.name,
            buy_price=price_val,
            sell_price=price_val,
            fetched_at=datetime.now(timezone.utc),
        )

