"""
app/domains/commodities/schemas.py
=====================================
Same three-tier discipline as crypto's schemas.py. `RawCommodityPrice`
is the internal shape every TSETMC response gets normalized into — each
field below is commented with exactly which TSETMC API field it came
from and which endpoint, since this was reverse-engineered from the
`tsetmc` PyPI package's source (github.com/5j9/tsetmc) rather than from
official TSETMC documentation (which doesn't really exist publicly).
Two confidence levels, both noted explicitly:

- Fields from `Instrument/GetInstrumentInfo` and `ClosingPrice/GetClosingPriceInfo`
  are HIGH confidence — verified against that package's typed `InstrumentInfo`/
  `ClosingPriceInfo` TypedDicts (real, checked-in source, not guessed).
- Fields from `ClosingPrice/GetTradeTop/CommodityFund` (the bulk "all gold
  ETFs in one call" endpoint) are LOWER confidence — that package parses
  it with `pandas.json_normalize` and no typed schema, so field names
  here are inferred from TSETMC's naming conventions seen elsewhere in
  the API (which are consistent enough to trust reasonably, but this
  specific endpoint's response was never independently observed). See
  `clients/tsetmc.py::fetch_bulk_commodity_funds` for how this is handled
  defensively (missing/renamed fields degrade to `None`, not a crash).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from app.domains.commodities.registry import CommodityGroup


class RawCommodityPrice(BaseModel):
    model_config = ConfigDict(frozen=True)

    ins_code: str
    isin: str
    group: CommodityGroup
    short_name: Optional[str] = None   # InstrumentInfo.lVal18 — Latin transliteration, e.g. "Noghran"
    symbol_fa: Optional[str] = None    # InstrumentInfo.lVal18AFC — Farsi ticker symbol as shown on tsetmc.com, e.g. "نقران"
    full_name: Optional[str] = None    # InstrumentInfo.lVal30

    last_price: Optional[float] = None      # ClosingPriceInfo.pDrCotVal — most recent trade price ("آخرین قیمت معامله")
    closing_price: Optional[float] = None   # ClosingPriceInfo.pClosing — official/final settlement price ("قیمت پایانی") — this is the "final price"
    previous_close: Optional[float] = None  # ClosingPriceInfo.priceYesterday
    open_price: Optional[float] = None      # ClosingPriceInfo.priceFirst
    day_low: Optional[float] = None         # ClosingPriceInfo.priceMin
    day_high: Optional[float] = None        # ClosingPriceInfo.priceMax
    change_amount: Optional[float] = None   # ClosingPriceInfo.priceChange — today's change vs. yesterday's close
    change_percent: Optional[float] = None  # computed: change_amount / previous_close * 100 — today's raise/drop %
    change_percent_month: Optional[float] = None  # computed live from ClosingPrice/GetClosingPriceDailyList — closing_price vs. ~30 days ago. None if that endpoint had no record that old (e.g. newly listed instrument), not persisted anywhere — recomputed on every fetch
    change_percent_year: Optional[float] = None   # same, vs. ~365 days ago

    volume: Optional[float] = None            # ClosingPriceInfo.qTotTran5J
    value: Optional[float] = None             # ClosingPriceInfo.qTotCap
    trade_count: Optional[float] = None       # ClosingPriceInfo.zTotTran
    avg_volume_5d: Optional[float] = None     # InstrumentInfo.qTotTran5JAvg — average traded volume over the last 5 sessions

    nav: Optional[float] = None        # computed: midpoint of (redemption_price, subscription_price) — NOT InstrumentInfo.nav, which TSETMC always returns as 0/null for every instrument type (verified against the reference tsetmc package's own docs, even for ordinary stocks), so it's never a usable source
    week_low: Optional[float] = None   # InstrumentInfo.minWeek
    week_high: Optional[float] = None  # InstrumentInfo.maxWeek
    year_low: Optional[float] = None   # InstrumentInfo.minYear
    year_high: Optional[float] = None  # InstrumentInfo.maxYear
    units_issued: Optional[float] = None  # InstrumentInfo.etfIssuedUnit — total fund units outstanding

    trading_status: Optional[str] = None  # ClosingPriceInfo.instrumentState.cEtavalTitle — e.g. "مجاز" (normal trading); surfaced so a halted instrument isn't shown as if its price were live
    price_band_min: Optional[float] = None  # InstrumentInfo.staticThreshold.psGelStaMin — today's exchange-imposed floor price
    price_band_max: Optional[float] = None  # InstrumentInfo.staticThreshold.psGelStaMax — today's exchange-imposed ceiling price

    redemption_price: Optional[float] = None    # ETF.pRedTran
    subscription_price: Optional[float] = None  # ETF.pSubTran

    fetched_at: datetime


# ---- API response models ----

class CommodityOut(BaseModel):
    ins_code: str
    isin: str
    group: str
    short_name: Optional[str] = None
    symbol_fa: Optional[str] = None
    full_name: Optional[str] = None
    last_price: Optional[float] = None
    closing_price: Optional[float] = None  # official/final price ("قیمت پایانی")
    previous_close: Optional[float] = None
    open_price: Optional[float] = None
    day_low: Optional[float] = None
    day_high: Optional[float] = None
    change_amount: Optional[float] = None
    change_percent: Optional[float] = None        # today's change, %
    change_percent_month: Optional[float] = None  # vs. closing_price ~30 days ago, computed live each fetch — see clients/tsetmc.py
    change_percent_year: Optional[float] = None   # vs. closing_price ~365 days ago, same
    volume: Optional[float] = None
    value: Optional[float] = None
    trade_count: Optional[float] = None
    avg_volume_5d: Optional[float] = None
    nav: Optional[float] = None
    week_low: Optional[float] = None
    week_high: Optional[float] = None
    year_low: Optional[float] = None
    year_high: Optional[float] = None
    units_issued: Optional[float] = None
    trading_status: Optional[str] = None
    price_band_min: Optional[float] = None
    price_band_max: Optional[float] = None
    redemption_price: Optional[float] = None
    subscription_price: Optional[float] = None
    fetched_at: str


class CommodityErrorOut(BaseModel):
    ins_code: str
    error: str


class CommodityListOut(BaseModel):
    group: str
    instruments: List[CommodityOut]
    errors: List[CommodityErrorOut] = []  # instruments that failed this round — partial results, not a 500
    fetched_at: str
    from_cache: bool = False


class TodayChangeItemOut(BaseModel):
    ins_code: str
    isin: str
    short_name: Optional[str] = None
    symbol_fa: Optional[str] = None
    last_price: Optional[float] = None
    change_amount: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[float] = None


class TodayChangesOut(BaseModel):
    group: str
    items: List[TodayChangeItemOut]  # sorted by change_percent, descending
    fetched_at: str


# ---- Gold retail-price platforms (physical/retail gold price, distinct from
# TSETMC's exchange-traded fund tracking above — see clients/platform_base.py) ----

class RawGoldPlatformPrice(BaseModel):
    """
    Internal, cross-platform normalized shape. UNVERIFIED FIELD MAPPING —
    see the module docstring in `clients/platform_base.py` and every
    concrete client file before trusting these values. Built from the
    URLs you supplied; none of the six platforms' actual JSON response
    shapes could be independently confirmed (all six block automated
    fetching, and none publish public API docs) — each client's parsing
    is best-effort against the endpoint's naming conventions, with a
    diagnostic error (includes the raw response's top-level keys) if the
    expected fields aren't found, rather than a silent wrong value.
    """
    model_config = ConfigDict(frozen=True)

    platform: str
    buy_price: Optional[float] = None
    sell_price: Optional[float] = None
    unit: str = "gram_18k"     # Iran's standard retail quote unit — see platform_base.py
    currency: str = "IRR"      # some of these platforms may actually quote in Toman (IRR/10) — VERIFY, see same docstring
    fetched_at: datetime


class GoldPlatformPriceOut(BaseModel):
    platform: str
    buy_price: Optional[float] = None
    sell_price: Optional[float] = None
    unit: str
    currency: str
    fetched_at: str


class GoldPlatformErrorOut(BaseModel):
    platform: str
    error: str


class GoldPlatformPricesOut(BaseModel):
    platforms: List[GoldPlatformPriceOut]
    errors: List[GoldPlatformErrorOut] = []
    fetched_at: str
    from_cache: bool = False
