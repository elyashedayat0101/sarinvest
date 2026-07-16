"""
app/domains/commodities/clients/digikala.py
================================================
UNVERIFIED — see clients/platform_base.py's module docstring.

The most uncertain of the six. `api.digikala.com/non-inventory/v1/prices/`
reads like a general-purpose price ticker (Digikala is a large Iranian
e-commerce marketplace, not a gold platform specifically) rather than a
gold-only endpoint — it likely returns a list covering several
commodities/currencies (gold, USD, coins, ...), and this client has to
find the gold entry within it rather than assume the whole payload is
gold data. If the response turns out to be gold-only after all, this
still works (falls through to treating the payload itself as the entry).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient, find_number
from app.domains.commodities.exceptions import GoldPlatformUnavailableError
from app.domains.commodities.schemas import RawGoldPlatformPrice

_GOLD_HINTS = ("gold", "طلا", "xau", "18")


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

        entry = self._find_gold_entry(payload)
        if entry is None:
            keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
            raise GoldPlatformUnavailableError(
                f"{self.name}: couldn't identify a gold-related entry in the response "
                f"(top-level shape: {keys}). This endpoint may return a general price "
                f"list — update _find_gold_entry to match the real structure."
            )

        buy = find_number(entry, ("buy",), ("buyPrice",), ("buy_price",), ("min",))
        sell = find_number(entry, ("sell",), ("sellPrice",), ("sell_price",), ("max",))
        price = find_number(entry, ("price",), ("value",), ("amount",))

        return RawGoldPlatformPrice(
            platform=self.name,
            buy_price=buy if buy is not None else price,
            sell_price=sell if sell is not None else price,
            fetched_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _find_gold_entry(payload: Any) -> Optional[dict]:
        candidates = []
        if isinstance(payload, dict):
            for key in ("data", "result", "items", "prices"):
                if isinstance(payload.get(key), list):
                    candidates = payload[key]
                    break
            else:
                candidates = [payload]  # maybe the payload itself is already the gold entry
        elif isinstance(payload, list):
            candidates = payload

        for item in candidates:
            if not isinstance(item, dict):
                continue
            haystack = " ".join(str(v) for v in item.values() if isinstance(v, str)).lower()
            haystack += " " + " ".join(str(k) for k in item.keys()).lower()
            if any(hint in haystack for hint in _GOLD_HINTS):
                return item
        return None
