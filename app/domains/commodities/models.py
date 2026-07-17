"""
app/domains/commodities/models.py
====================================
Registered on `SharedBase`, same as `crypto` and `users` — see
ARCHITECTURE.md for why every domain from `crypto` onward shares one
database instead of getting its own file.

One table, richer than crypto's `PriceSnapshot` because TSETMC gives a
lot more per instrument than an exchange ticker does (NAV, 52-week
range, trade count) — see `schemas.py::RawCommodityPrice` for the full
field-by-field provenance (which TSETMC endpoint each column came from).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import SharedBase


class CommodityPriceSnapshot(SharedBase):
    __tablename__ = "commodity_price_snapshots"
    __table_args__ = (
        Index("idx_commodity_ins_time", "ins_code", "fetched_at"),
        Index("idx_commodity_group_time", "group", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ins_code: Mapped[str]
    isin: Mapped[str]
    group: Mapped[str]  # "gold" | "silver" | ... — see registry.py::CommodityGroup
    short_name: Mapped[Optional[str]]
    symbol_fa: Mapped[Optional[str]]  # lVal18AFC — Farsi ticker symbol
    full_name: Mapped[Optional[str]]

    last_price: Mapped[Optional[float]]       # pDrCotVal — last traded price
    closing_price: Mapped[Optional[float]]  # pClosing — official/final price ("قیمت پایانی")
    previous_close: Mapped[Optional[float]]   # priceYesterday
    open_price: Mapped[Optional[float]]       # priceFirst
    day_low: Mapped[Optional[float]]          # priceMin
    day_high: Mapped[Optional[float]]         # priceMax
    change_amount: Mapped[Optional[float]]    # priceChange — today's change, as-is from TSETMC
    change_percent: Mapped[Optional[float]]   # computed: change_amount / previous_close * 100

    volume: Mapped[Optional[float]]           # qTotTran5J
    value: Mapped[Optional[float]]            # qTotCap (rial)
    trade_count: Mapped[Optional[float]]      # zTotTran
    avg_volume_5d: Mapped[Optional[float]]  # qTotTran5JAvg — average volume, last 5 sessions

    nav: Mapped[Optional[
        float]]  # computed: midpoint of redemption_price/subscription_price (see clients/tsetmc.py) — NOT the raw InstrumentInfo.nav field, which TSETMC never populates
    week_low: Mapped[Optional[float]]         # minWeek
    week_high: Mapped[Optional[float]]        # maxWeek
    year_low: Mapped[Optional[float]]         # minYear
    year_high: Mapped[Optional[float]]        # maxYear
    units_issued: Mapped[Optional[float]]  # etfIssuedUnit — total fund units outstanding

    trading_status: Mapped[Optional[str]]  # instrumentState.cEtavalTitle, e.g. "مجاز"
    price_band_min: Mapped[Optional[float]]  # staticThreshold.psGelStaMin — today's allowed floor
    price_band_max: Mapped[Optional[float]]  # staticThreshold.psGelStaMax — today's allowed ceiling

    redemption_price: Mapped[Optional[float]]   # ETF.pRedTran — NAV for selling back to the fund
    subscription_price: Mapped[Optional[float]]  # ETF.pSubTran — NAV for buying from the fund

    fetched_at: Mapped[
        str]  # ISO 8601. NOTE: change_percent_month/year are NOT columns here — computed live from TSETMC on every fetch, never persisted; see clients/tsetmc.py and service.py
