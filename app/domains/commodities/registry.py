"""
app/domains/commodities/registry.py
======================================
Same pattern as `legacy/fund_config.py::FUNDS` — a static registry of
known instruments, keyed by group. Adding silver later is exactly
"append entries with group='silver'" — no code changes anywhere else in
this domain, since every service/repository/router method here already
takes `group` as a parameter rather than hardcoding "gold".

Deliberately NOT auto-discovering instruments from TSETMC's commodity-fund
listing (`clients/tsetmc.py::fetch_bulk_commodity_funds` could do this)
and using that as the source of truth — that endpoint returns everything
tradable on the Mercantile exchange, which may include non-gold-backed
funds; a hand-curated, reviewed list is safer for "which instruments does
my app treat as gold" than "whatever TSETMC's bulk listing happens to
contain today." The bulk endpoint is still useful — see `service.py` — as
an efficient way to *refresh* data for known instruments, just not as the
list of which instruments exist.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel

CommodityGroup = Literal["gold", "silver"]


class InstrumentRef(BaseModel):
    ins_code: str
    isin: str
    group: CommodityGroup


GOLD_ETF_INSTRUMENTS: List[InstrumentRef] = [
    InstrumentRef(ins_code="56987424987755487", isin="IRTKATSH0001", group="gold"),
    InstrumentRef(ins_code="28374437855144739", isin="IRTKALTN0001", group="gold"),
    InstrumentRef(ins_code="30895446582685604", isin="IRTKEMRL0001", group="gold"),
    InstrumentRef(ins_code="9089296888187061", isin="IRTKTABA0001", group="gold"),
    InstrumentRef(ins_code="38544104313215500", isin="IRTKJAVA0001", group="gold"),
    InstrumentRef(ins_code="61805666737517582", isin="IRTKDRKS0001", group="gold"),
    InstrumentRef(ins_code="17248898258246807", isin="IRTKDORN0001", group="gold"),
    InstrumentRef(ins_code="20244389840999638", isin="IRTKROSE0001", group="gold"),
    InstrumentRef(ins_code="33254899395816171", isin="IRTKZARF0001", group="gold"),
    InstrumentRef(ins_code="33144542989832366", isin="IRTKZFAM0001", group="gold"),
    InstrumentRef(ins_code="28255729477187163", isin="IRTKZARV0001", group="gold"),
    InstrumentRef(ins_code="16817885126368964", isin="IRTKZARG0001", group="gold"),
    InstrumentRef(ins_code="64795751499397128", isin="IRTKZOMR0001", group="gold"),
    InstrumentRef(ins_code="46700660505281786", isin="IRTKLOTF0001", group="gold"),
    InstrumentRef(ins_code="32469128621155736", isin="IRTKZARA0001", group="gold"),
    InstrumentRef(ins_code="30582275818828857", isin="IRTKNAAB0001", group="gold"),
    InstrumentRef(ins_code="58514988269776425", isin="IRTKGANJ0001", group="gold"),
    InstrumentRef(ins_code="12390706505809150", isin="IRTKKIAN0001", group="gold"),
    InstrumentRef(ins_code="4626686276232042", isin="IRTKNAFS0001", group="gold"),
    InstrumentRef(ins_code="6362118829011821", isin="IRTKLIAN0001", group="gold"),
    InstrumentRef(ins_code="68376789401977331", isin="IRTKGOLN0001", group="gold"),
    InstrumentRef(ins_code="25559236668122210", isin="IRTKROBA0001", group="gold"),
    InstrumentRef(ins_code="6237807001018762", isin="IRTKGHIR0001", group="gold"),
    InstrumentRef(ins_code="34144395039913458", isin="IRTKMOFD0001", group="gold"),
    InstrumentRef(ins_code="14035144070182412", isin="IRTKRITO0001", group="gold"),
    InstrumentRef(ins_code="50072269736641214", isin="IRTKHAMY0001", group="gold"),
    InstrumentRef(ins_code="35389487611786089", isin="IRTKJAMF0001", group="gold"),
    InstrumentRef(ins_code="53633583359422860", isin="IRTKMIRA0001", group="gold"),
    InstrumentRef(ins_code="17244733069907210", isin="IRTKROZG0001", group="gold"),
    InstrumentRef(ins_code="53514992320442853", isin="IRTKROZG0001", group="gold"),  # NOTE: duplicate isin as above — kept as given, not deduplicated (see README_MIGRATION-style caveat in this domain's docs)
    InstrumentRef(ins_code="48968268685622891", isin="IRTKGOLD0001", group="gold"),
    InstrumentRef(ins_code="13117618204212939", isin="IRTKDAFI0001", group="gold"),
]

# Populate this the same way when you're ready — nothing else in this
# domain needs to change. Router/service/repository all take `group` as
# a parameter already.
SILVER_ETF_INSTRUMENTS: List[InstrumentRef] = []

_ALL_INSTRUMENTS: List[InstrumentRef] = GOLD_ETF_INSTRUMENTS + SILVER_ETF_INSTRUMENTS


def instruments_for_group(group: CommodityGroup) -> List[InstrumentRef]:
    return [i for i in _ALL_INSTRUMENTS if i.group == group]


def all_ins_codes() -> List[str]:
    return [i.ins_code for i in _ALL_INSTRUMENTS]


def group_for_ins_code(ins_code: str) -> CommodityGroup | None:
    for i in _ALL_INSTRUMENTS:
        if i.ins_code == ins_code:
            return i.group
    return None
