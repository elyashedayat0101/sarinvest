"""
app/domains/commodities/clients/technogold.py
==================================================
UNVERIFIED — see clients/platform_base.py's module docstring.

`api2.technogold.gold/customer/tradeables/price-history?type=weekly` —
"tradeables" (plural) plus "price-history" suggests this may return
multiple tradeable products, each with a historical series, rather than
a single gold price directly. This client takes the first tradeable
entry found and its most recent history point — if there are multiple
tradeables and gold isn't first, this needs a real response in hand to
fix (see the diagnostic error message).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient, find_number
from app.domains.commodities.exceptions import GoldPlatformUnavailableError
from app.domains.commodities.schemas import RawGoldPlatformPrice


class TechnoGoldClient(GoldPricePlatformClient):
    name = "technogold"
    _URL = "https://api2.technogold.gold/customer/tradeables/price-history"
    _HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def fetch_price(self) -> RawGoldPlatformPrice:
        try:
            resp = await self._http.get(self._URL, params={"type": "weekly"}, headers=self._HEADERS)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as e:
            raise GoldPlatformUnavailableError(f"{self.name}: request failed: {e}") from e

        last_point = self._first_tradeable_last_point(payload)
        if last_point is None:
            keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
            raise GoldPlatformUnavailableError(
                f"{self.name}: couldn't find a tradeable/history series in the response "
                f"(top-level shape: {keys}). Update _first_tradeable_last_point to match."
            )

        buy = find_number(last_point, ("buy",), ("buyPrice",), ("bid",))
        sell = find_number(last_point, ("sell",), ("sellPrice",), ("ask",))
        price = find_number(last_point, ("price",), ("close",), ("value",))

        return RawGoldPlatformPrice(
            platform=self.name,
            buy_price=buy if buy is not None else price,
            sell_price=sell if sell is not None else price,
            fetched_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _first_tradeable_last_point(payload: Any) -> Optional[dict]:
        tradeables = None
        if isinstance(payload, list):
            tradeables = payload
        elif isinstance(payload, dict):
            for key in ("data", "result", "tradeables", "items"):
                if isinstance(payload.get(key), list):
                    tradeables = payload[key]
                    break

        if not tradeables:
            return None

        first = tradeables[0]
        history = None
        if isinstance(first, dict):
            for key in ("history", "prices", "priceHistory", "data", "points"):
                if isinstance(first.get(key), list):
                    history = first[key]
                    break
            if history is None:
                return first  # maybe `first` already IS a single price point, not a series
        elif isinstance(first, list):
            history = first

        if not history:
            return None
        last = history[-1]
        return last if isinstance(last, dict) else None
