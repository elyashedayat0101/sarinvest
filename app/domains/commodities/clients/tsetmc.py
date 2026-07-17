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

1. `fetch_one`/`fetch_many` — per-instrument, four concurrent calls per
   instrument, merged into one `RawCommodityPrice`:
   - `GetInstrumentInfo` + `GetClosingPriceInfo` — mandatory; a failure
     here fails the whole instrument. Field names verified against the
     `tsetmc` package's typed `InstrumentInfo`/`ClosingPriceInfo`
     TypedDicts — high confidence.
   - `GetETFByInsCode` — best-effort; 404s for non-ETF instruments.
   - `ClosingPrice/GetClosingPriceDailyList/{insCode}/{n}` — best-effort;
     returns up to `n` days of real trading-day closing prices, used to
     compute `change_percent_month`/`change_percent_year` live on every
     fetch (no DB involved). Verified against 5j9/tsetmc==0.48.2's
     source (`instruments.py::daily_closing_price`) — NOT the same as
     `closingPriceInfo.thirtyDayClosingHistory`, which that same source
     confirms is always null (a dead field, same pattern as
     `InstrumentInfo.nav` — see schemas.py). This is the default path
     `service.py` uses.

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
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import httpx
from pydantic import BaseModel, ValidationError

from app.domains.commodities.clients.base import CommodityDataClient
from app.domains.commodities.exceptions import TsetmcUnavailableError
from app.domains.commodities.registry import InstrumentRef
from app.domains.commodities.schemas import RawCommodityPrice

log = logging.getLogger("commodities.tsetmc")

_FARSI_NORM = str.maketrans("يك", "یک")  # same normalization the reference package applies to `fa=True` responses


def _yyyymmdd(d) -> int:
    """Gregorian date -> TSETMC's dEven int format, e.g. 2026-07-15 -> 20260715."""
    return int(d.strftime("%Y%m%d"))


def _reference_price(history: List["_DailyClosingRecord"], target: int) -> Optional[float]:
    """Closing price of the most recent trading day at or before `target`
    (a dEven-format int) — i.e. "the last known price as of that date",
    not an interpolation. Returns None if `history` has no record that old
    (e.g. the instrument hasn't been trading that long, or the history
    fetch degraded — see fetch_one), which the caller treats as "not
    available" rather than an error."""
    candidates = [r for r in history if r.dEven is not None and r.pClosing is not None and r.dEven <= target]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.dEven).pClosing


def _pct_change(current: Optional[float], reference: Optional[float]) -> Optional[float]:
    if current is None or not reference:
        return None
    return round((current - reference) / reference * 100, 4)


# ---- Per-endpoint raw response models (verified field names) ----

class _StaticThreshold(BaseModel):
    """Daily allowed price band (دامنه نوسان روزانه) — the exchange-imposed
    ceiling/floor for the day, distinct from priceMin/priceMax below which
    are the *actual* traded high/low within that band."""
    model_config = {"extra": "ignore"}
    psGelStaMax: Optional[float] = None
    psGelStaMin: Optional[float] = None


class _InstrumentState(BaseModel):
    """Live trading-status flag. cEtavalTitle is the Persian human-readable
    status (e.g. "مجاز" = normal/allowed trading; other TSETMC values
    include halted/suspended states) — worth surfacing since a UI showing
    a price with no indication trading is halted is misleading."""
    model_config = {"extra": "ignore"}
    cEtaval: Optional[str] = None
    cEtavalTitle: Optional[str] = None


class _InstrumentInfo(BaseModel):
    model_config = {"extra": "ignore"}
    insCode: str
    lVal18: Optional[str] = None
    lVal18AFC: Optional[str] = None  # Farsi/AFC-charset trading symbol, e.g. "نقران" — the symbol actually shown on tsetmc.com, vs. lVal18's Latin transliteration
    lVal30: Optional[str] = None
    nav: Optional[float] = None
    minWeek: Optional[float] = None
    maxWeek: Optional[float] = None
    minYear: Optional[float] = None
    maxYear: Optional[float] = None
    qTotTran5JAvg: Optional[float] = None  # average daily traded volume over the last 5 sessions
    etfIssuedUnit: Optional[float] = None  # total fund units outstanding
    staticThreshold: Optional[_StaticThreshold] = None


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
    instrumentState: Optional[_InstrumentState] = None


class _EtfInfo(BaseModel):
    model_config = {"extra": "ignore"}
    insCode: str
    pRedTran: Optional[float] = None
    pSubTran: Optional[float] = None


class _DailyClosingRecord(BaseModel):
    """One row from ClosingPrice/GetClosingPriceDailyList — a REAL, working
    historical-price endpoint (unlike closingPriceInfo.thirtyDayClosingHistory,
    which the reference package's own docs confirm is always null, same dead-
    field pattern as InstrumentInfo.nav). Verified against
    5j9/tsetmc==0.48.2's instruments.py: `daily_closing_price()` calls
    `ClosingPrice/GetClosingPriceDailyList/{insCode}/{n}` -> `j['closingPriceDaily']`,
    a list of these. dEven is Gregorian YYYYMMDD (confirmed against the
    instrument's own dEven/finalLastDate elsewhere in a live response)."""
    model_config = {"extra": "ignore"}
    dEven: Optional[int] = None
    pClosing: Optional[float] = None


class TsetmcClient(CommodityDataClient):
    name = "tsetmc"

    _BASE_URL = "https://cdn.tsetmc.com/api/"
    _HEADERS = {
        # TSETMC's API rejects/behaves inconsistently without a browser-like UA.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:100.0) Gecko/20100101 Firefox/100.0",
    }

    def __init__(self, http: httpx.AsyncClient, max_concurrent: int = 8, history_days: int = 290):
        self._http = http
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # 290 trading-day records comfortably covers a calendar year even
        # accounting for Iran's Thu/Fri weekend + holidays (~245 trading
        # days/year in practice) — see _reference_price below.
        self._history_days = history_days

    # ------------------------------------------------------------------ #
    # Per-instrument (verified, default path)
    # ------------------------------------------------------------------ #
    async def fetch_one(self, instrument: InstrumentRef) -> RawCommodityPrice:
        async with self._semaphore:
            try:
                info_j, price_j = await asyncio.gather(
                    self._get_json(f"Instrument/GetInstrumentInfo/{instrument.ins_code}", farsi=True),
                    self._get_json(f"ClosingPrice/GetClosingPriceInfo/{instrument.ins_code}", farsi=True),
                )
            except httpx.HTTPError as e:
                raise TsetmcUnavailableError(f"tsetmc: request failed for {instrument.ins_code}: {e}") from e

            try:
                info = _InstrumentInfo.model_validate(info_j["instrumentInfo"])
                price = _ClosingPriceInfo.model_validate(price_j["closingPriceInfo"])
            except (ValidationError, KeyError) as e:
                raise TsetmcUnavailableError(f"tsetmc: unexpected response shape for {instrument.ins_code}: {e}") from e

            # ETF data and daily history are both "nice to have", fetched
            # concurrently, each independently tolerant of failure:
            # non-ETF instruments 404 on the ETF endpoint, and a freshly
            # listed fund simply won't have a year of history yet. Neither
            # failure should sink the whole instrument the way a failure
            # of the two calls above does.
            etf_result, history_result = await asyncio.gather(
                self._get_json(f"Fund/GetETFByInsCode/{instrument.ins_code}", farsi=False),
                self._get_json(f"ClosingPrice/GetClosingPriceDailyList/{instrument.ins_code}/{self._history_days}", farsi=False),
                return_exceptions=True,
            )

            etf: Optional[_EtfInfo] = None
            if not isinstance(etf_result, Exception):
                try:
                    etf = _EtfInfo.model_validate(etf_result["etf"])
                except (ValidationError, KeyError, TypeError):
                    pass

            daily_history: List[_DailyClosingRecord] = []
            if isinstance(history_result, Exception):
                log.debug("tsetmc: daily history fetch failed for %s: %s", instrument.ins_code, history_result)
            else:
                try:
                    daily_history = [_DailyClosingRecord.model_validate(r) for r in history_result["closingPriceDaily"]]
                except (ValidationError, KeyError, TypeError):
                    log.debug("tsetmc: daily history parse failed for %s", instrument.ins_code)

            change_percent = None
            if price.priceChange is not None and price.priceYesterday:
                change_percent = round(price.priceChange / price.priceYesterday * 100, 4)

            nav = None
            if etf and etf.pRedTran is not None and etf.pSubTran is not None:
                nav = round((etf.pRedTran + etf.pSubTran) / 2, 4)

            today = datetime.now(timezone.utc).date()
            month_ago_price = _reference_price(daily_history, _yyyymmdd(today - timedelta(days=30)))
            year_ago_price = _reference_price(daily_history, _yyyymmdd(today - timedelta(days=365)))
            change_percent_month = _pct_change(price.pClosing, month_ago_price)
            change_percent_year = _pct_change(price.pClosing, year_ago_price)

            return RawCommodityPrice(
                ins_code=instrument.ins_code, isin=instrument.isin, group=instrument.group,
                short_name=info.lVal18, symbol_fa=info.lVal18AFC, full_name=info.lVal30,
                last_price=price.pDrCotVal, closing_price=price.pClosing,
                previous_close=price.priceYesterday, open_price=price.priceFirst,
                day_low=price.priceMin, day_high=price.priceMax,
                change_amount=price.priceChange, change_percent=change_percent,
                change_percent_month=change_percent_month, change_percent_year=change_percent_year,
                volume=price.qTotTran5J, value=price.qTotCap, trade_count=price.zTotTran,
                avg_volume_5d=info.qTotTran5JAvg,
                nav=nav, week_low=info.minWeek, week_high=info.maxWeek,
                year_low=info.minYear, year_high=info.maxYear,
                units_issued=info.etfIssuedUnit,
                trading_status=price.instrumentState.cEtavalTitle if price.instrumentState else None,
                price_band_min=info.staticThreshold.psGelStaMin if info.staticThreshold else None,
                price_band_max=info.staticThreshold.psGelStaMax if info.staticThreshold else None,
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
