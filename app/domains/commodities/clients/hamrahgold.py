"""
app/domains/commodities/clients/hamrahgold.py
==================================================
UNVERIFIED — see clients/platform_base.py's module docstring before
trusting this.

The URL you supplied (`?start=-1w&every=1d&type=buy`) is a time-series
endpoint, not a "current price" one — `start`/`every` are historical
range/granularity params, and `type=buy` vs `type=sell` look like two
separate calls are needed for the two sides of the spread. This client
fetches both concurrently and takes the most recent point from each
series as "current." If the real response nests data differently than
guessed below, `find_number`'s diagnostic error will show you the actual
top-level shape — fix `_last_point_value` to match it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient, find_number
from app.domains.commodities.exceptions import GoldPlatformUnavailableError
from app.domains.commodities.schemas import RawGoldPlatformPrice


class HamrahGoldClient(GoldPricePlatformClient):
    name = "hamrahgold"
    _URL = "https://hamrahgold.com/api/market/price/xau"
    _HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def fetch_price(self) -> RawGoldPlatformPrice:
        try:
            buy_json, sell_json = await self._fetch_both()
        except httpx.HTTPError as e:
            raise GoldPlatformUnavailableError(f"{self.name}: request failed: {e}") from e

        buy = self._last_point_value(buy_json)
        sell = self._last_point_value(sell_json)
        if buy is None and sell is None:
            raise GoldPlatformUnavailableError(
                f"{self.name}: could not find a price series in the response. "
                f"Buy response keys: {list(buy_json.keys()) if isinstance(buy_json, dict) else type(buy_json).__name__}"
            )

        return RawGoldPlatformPrice(
            platform=self.name, buy_price=buy, sell_price=sell, fetched_at=datetime.now(timezone.utc),
        )

    async def _fetch_both(self):
        return await asyncio.gather(
            self._get_series("buy"),
            self._get_series("sell"),
        )

    async def _get_series(self, side: str) -> Any:
        resp = await self._http.get(
            self._URL, params={"start": "-1w", "every": "1d", "type": side}, headers=self._HEADERS
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _last_point_value(payload: Any) -> Optional[float]:
        """Guesses at a handful of common time-series envelope shapes and
        pulls the last point's value out of whichever one matches."""
        series = None
        if isinstance(payload, list):
            series = payload
        elif isinstance(payload, dict):
            for key in ("data", "result", "prices", "series", "items"):
                if isinstance(payload.get(key), list):
                    series = payload[key]
                    break
        if not series:
            return None

        last = series[-1]
        if isinstance(last, (int, float)):
            return float(last)
        if isinstance(last, list) and len(last) >= 2:
            # common [timestamp, value] pair shape
            return find_number({"v": last[1]}, ("v",))
        if isinstance(last, dict):
            return find_number(last, ("price",), ("value",), ("close",), ("p",))
        return None
