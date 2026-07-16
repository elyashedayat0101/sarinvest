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
    full_name: Mapped[Optional[str]]

    last_price: Mapped[Optional[float]]       # pDrCotVal — last traded price
    closing_price: Mapped[Optional[float]]    # pClosing — volume-weighted average / official close
    previous_close: Mapped[Optional[float]]   # priceYesterday
    open_price: Mapped[Optional[float]]       # priceFirst
    day_low: Mapped[Optional[float]]          # priceMin
    day_high: Mapped[Optional[float]]         # priceMax
    change_amount: Mapped[Optional[float]]    # priceChange — today's change, as-is from TSETMC
    change_percent: Mapped[Optional[float]]   # computed: change_amount / previous_close * 100

    volume: Mapped[Optional[float]]           # qTotTran5J
    value: Mapped[Optional[float]]            # qTotCap (rial)
    trade_count: Mapped[Optional[float]]      # zTotTran

    nav: Mapped[Optional[float]]              # InstrumentInfo.nav
    week_low: Mapped[Optional[float]]         # minWeek
    week_high: Mapped[Optional[float]]        # maxWeek
    year_low: Mapped[Optional[float]]         # minYear
    year_high: Mapped[Optional[float]]        # maxYear

    redemption_price: Mapped[Optional[float]]   # ETF.pRedTran — NAV for selling back to the fund
    subscription_price: Mapped[Optional[float]]  # ETF.pSubTran — NAV for buying from the fund

    fetched_at: Mapped[str]
