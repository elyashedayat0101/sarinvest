"""
app/domains/commodities/clients/tsetmc.py
=============================================
Implements calls against TSETMC's real API directly with `httpx`,
rather than depending on the `tsetmc` PyPI package (github.com/5j9/tsetmc)
at runtime — that package pulls in pandas/numpy/pyarrow/lxml for what we
need to be three small JSON GET requests, and (as of this writing) its
`aiohutils` dependency requires `warnings.deprecated`, a Python 3.13-only
stdlib addition, so it doesn't even import cleanly on 3.12. Its source
was still the right place to reverse-engineer the actual endpoints and
response shapes from — see the field-provenance comments in `schemas.py`.

Two fetch strategies, both implemented, for different confidence levels:

1. `fetch_one`/`fetch_many` — per-instrument, three concurrent calls
   (`GetInstrumentInfo` + `GetClosingPriceInfo` + `GetETFByInsCode`) per
   instrument, merged into one `RawCommodityPrice`. Field names here are
   verified against the `tsetmc` package's typed `InstrumentInfo`/
   `ClosingPriceInfo`/`ETF` TypedDicts — high confidence. This is the
   default path `service.py` uses.

2. `fetch_bulk_commodity_funds` — one HTTP call
   (`ClosingPrice/GetTradeTop/CommodityFund/{flow}/{top}`) returns every
   commodity ETF on the Mercantile exchange at once — the "get everything
   in one request" endpoint. Field names here are inferred (that
   endpoint isn't independently typed even in the reference package —
   see `schemas.py`'s docstring), so parsing is defensive: unknown/missing
   keys become `None` rather than raising, and a warning is logged if a
   response looks nothing like what was expected, rather than silently
   returning garbage.

A note on being a good API citizen: TSETMC has no published rate limits
or terms for this API (it's the data source the tsetmc.com website itself
calls), so `fetch_many` caps concurrency with a semaphore rather than
firing all requests at once — being conservative by default here is
cheap insurance against getting blocked, not a documented requirement.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx
from pydantic import BaseModel, ValidationError

from app.domains.commodities.clients.base import CommodityDataClient
from app.domains.commodities.exceptions import TsetmcUnavailableError
from app.domains.commodities.registry import InstrumentRef
from app.domains.commodities.schemas import RawCommodityPrice

log = logging.getLogger("commodities.tsetmc")

_FARSI_NORM = str.maketrans("يك", "یک")  # same normalization the reference package applies to `fa=True` responses


# ---- Per-endpoint raw response models (verified field names) ----

class _InstrumentInfo(BaseModel):
    model_config = {"extra": "ignore"}
    insCode: str
    lVal18: Optional[str] = None
    lVal30: Optional[str] = None
    nav: Optional[float] = None
    minWeek: Optional[float] = None
    maxWeek: Optional[float] = None
    minYear: Optional[float] = None
    maxYear: Optional[float] = None


class _ClosingPriceInfo(BaseModel):
    model_config = {"extra": "ignore"}
    insCode: str
    pDrCotVal: Optional[float] = None
    pClosing: Optional[float] = None
    priceYesterday: Optional[float] = None
    priceFirst: Optional[float] = None
    priceMin: Optional[float] = None
    priceMax: Optional[float] = None
    priceChange: Optional[float] = None
    qTotTran5J: Optional[float] = None
    qTotCap: Optional[float] = None
    zTotTran: Optional[float] = None


class _EtfInfo(BaseModel):
    model_config = {"extra": "ignore"}
    insCode: str
    pRedTran: Optional[float] = None
    pSubTran: Optional[float] = None


class TsetmcClient(CommodityDataClient):
    name = "tsetmc"

    _BASE_URL = "https://cdn.tsetmc.com/api/"
    _HEADERS = {
        # TSETMC's API rejects/behaves inconsistently without a browser-like UA.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:100.0) Gecko/20100101 Firefox/100.0",
    }

    def __init__(self, http: httpx.AsyncClient, max_concurrent: int = 8):
        self._http = http
        self._semaphore = asyncio.Semaphore(max_concurrent)

    # ------------------------------------------------------------------ #
    # Per-instrument (verified, default path)
    # ------------------------------------------------------------------ #
    async def fetch_one(self, instrument: InstrumentRef) -> RawCommodityPrice:
        async with self._semaphore:
            try:
                info_j, price_j, etf_j = await asyncio.gather(
                    self._get_json(f"Instrument/GetInstrumentInfo/{instrument.ins_code}", farsi=True),
                    self._get_json(f"ClosingPrice/GetClosingPriceInfo/{instrument.ins_code}", farsi=True),
                    self._get_json(f"Fund/GetETFByInsCode/{instrument.ins_code}", farsi=False),
                )
            except httpx.HTTPError as e:
                raise TsetmcUnavailableError(f"tsetmc: request failed for {instrument.ins_code}: {e}") from e

            try:
                info = _InstrumentInfo.model_validate(info_j["instrumentInfo"])
                price = _ClosingPriceInfo.model_validate(price_j["closingPriceInfo"])
            except (ValidationError, KeyError) as e:
                raise TsetmcUnavailableError(f"tsetmc: unexpected response shape for {instrument.ins_code}: {e}") from e

            # ETF endpoint can legitimately 404/error for a non-ETF instrument
            # or transient issues — degrade to missing redemption/subscription
            # data rather than failing the whole instrument for it.
            etf: Optional[_EtfInfo] = None
            try:
                etf = _EtfInfo.model_validate(etf_j["etf"])
            except (ValidationError, KeyError, TypeError):
                pass

            change_percent = None
            if price.priceChange is not None and price.priceYesterday:
                change_percent = round(price.priceChange / price.priceYesterday * 100, 4)

            return RawCommodityPrice(
                ins_code=instrument.ins_code, isin=instrument.isin, group=instrument.group,
                short_name=info.lVal18, full_name=info.lVal30,
                last_price=price.pDrCotVal, closing_price=price.pClosing,
                previous_close=price.priceYesterday, open_price=price.priceFirst,
                day_low=price.priceMin, day_high=price.priceMax,
                change_amount=price.priceChange, change_percent=change_percent,
                volume=price.qTotTran5J, value=price.qTotCap, trade_count=price.zTotTran,
                nav=info.nav, week_low=info.minWeek, week_high=info.maxWeek,
                year_low=info.minYear, year_high=info.maxYear,
                redemption_price=etf.pRedTran if etf else None,
                subscription_price=etf.pSubTran if etf else None,
                fetched_at=datetime.now(timezone.utc),
            )

    async def fetch_many(self, instruments: List[InstrumentRef]) -> Tuple[List[RawCommodityPrice], List[Tuple[str, str]]]:
        results = await asyncio.gather(
            *(self.fetch_one(i) for i in instruments), return_exceptions=True
        )
        successes: List[RawCommodityPrice] = []
        errors: List[Tuple[str, str]] = []
        for instrument, result in zip(instruments, results):
            if isinstance(result, RawCommodityPrice):
                successes.append(result)
            elif isinstance(result, Exception):
                log.warning("fetch failed for %s: %s", instrument.ins_code, result)
                errors.append((instrument.ins_code, str(result)))
        return successes, errors

    # ------------------------------------------------------------------ #
    # Bulk (unverified field names — defensive parsing, see module docstring)
    # ------------------------------------------------------------------ #
    async def fetch_bulk_commodity_funds(self, flow: str = "7", top: str = "9999") -> List[dict]:
        """Returns raw dicts (not RawCommodityPrice) — caller
        (service.py::refresh_via_bulk) is responsible for cross-referencing
        against the known instrument registry by `insCode` and mapping
        into RawCommodityPrice, tolerating whatever fields are actually
        present."""
        try:
            j = await self._get_json(f"ClosingPrice/GetTradeTop/CommodityFund/{flow}/{top}", farsi=True)
        except httpx.HTTPError as e:
            raise TsetmcUnavailableError(f"tsetmc: bulk commodity fund request failed: {e}") from e

        rows = j.get("tradeTop")
        if not isinstance(rows, list):
            log.warning("tsetmc bulk response missing expected 'tradeTop' list — got keys: %s", list(j.keys()))
            return []
        return rows

    # ------------------------------------------------------------------ #
    # Private
    # ------------------------------------------------------------------ #
    async def _get_json(self, path: str, *, farsi: bool) -> dict:
        resp = await self._http.get(f"{self._BASE_URL}{path}", headers=self._HEADERS)
        resp.raise_for_status()
        text = resp.text
        if farsi:
            text = text.translate(_FARSI_NORM)
        return json.loads(text)
