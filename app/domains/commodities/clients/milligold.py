"""
app/domains/commodities/clients/milligold.py
==================================================
UNVERIFIED — see clients/platform_base.py's module docstring.

`milli.gold/api/v1/public/milli-price/external` — "external" strongly
suggests this is meant to be consumed by other sites/apps (i.e. closer
to a real public price feed than the others), which is a good sign for
stability, but the field names are still unconfirmed.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient, find_number, require_parsed
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

        data = payload.get("data", payload) if isinstance(payload, dict) else payload

        buy = find_number(data, ("buy",), ("buyPrice",), ("buy_price",))
        sell = find_number(data, ("sell",), ("sellPrice",), ("sell_price",))
        price = find_number(data, ("price",), ("value",), ("milliPrice",), ("amount",))

        fields = require_parsed(self.name, payload, buy=buy, sell=sell, price=price)

        return RawGoldPlatformPrice(
            platform=self.name,
            buy_price=fields["buy"] if fields["buy"] is not None else fields["price"],
            sell_price=fields["sell"] if fields["sell"] is not None else fields["price"],
            fetched_at=datetime.now(timezone.utc),
        )
