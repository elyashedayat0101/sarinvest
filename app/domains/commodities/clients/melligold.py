"""
app/domains/commodities/clients/melligold.py
==================================================
UNVERIFIED — see clients/platform_base.py's module docstring.

`melligold.com/api/v1/exchange/buy-sell-price/?symbol=XAU18&format=json`
is the most explicitly-named endpoint of the six ("buy-sell-price"),
which is a decent sign the response has literal `buy`/`sell` keys — but
still unconfirmed. `symbol=XAU18` is passed through as given (18-karat
gold spot symbol convention).
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient, find_number, require_parsed
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

        data = payload.get("data", payload) if isinstance(payload, dict) else payload

        buy = find_number(data, ("buy",), ("buyPrice",), ("buy_price",))
        sell = find_number(data, ("sell",), ("sellPrice",), ("sell_price",))

        fields = require_parsed(self.name, payload, buy=buy, sell=sell)

        return RawGoldPlatformPrice(
            platform=self.name, buy_price=fields["buy"], sell_price=fields["sell"],
            fetched_at=datetime.now(timezone.utc),
        )
