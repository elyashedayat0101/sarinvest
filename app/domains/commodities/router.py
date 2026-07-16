"""
app/domains/commodities/router.py
====================================
Routes take `group` as a path segment (`/gold`, and `/silver` the moment
`registry.py::SILVER_ETF_INSTRUMENTS` is populated — no new route needed,
`get_all`/`get_today_changes` already take `group` as a parameter and
`UnknownGroupError` -> 400 already handles an empty/unpopulated group
cleanly).

Mounted at `/api/v1/commodities` — versioned, like every domain added
after `crypto` (see ARCHITECTURE.md's versioning section).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.domains.commodities.deps import get_commodity_service, get_gold_platform_service
from app.domains.commodities.platform_service import GoldPlatformPriceService
from app.domains.commodities.schemas import CommodityListOut, CommodityOut, GoldPlatformPricesOut, TodayChangesOut
from app.domains.commodities.service import CommodityService

router = APIRouter(prefix="/api/v1/commodities", tags=["commodities"])


@router.get("/gold/platforms", response_model=GoldPlatformPricesOut)
async def gold_platform_prices(service: GoldPlatformPriceService = Depends(get_gold_platform_service)):
    """
    Retail/physical gold price across external platforms (hamrahgold,
    digikala, technogold, talasea, milligold, melligold) — distinct from
    `GET /gold` above, which is TSETMC exchange-traded fund data. Backed
    by a cache refreshed every ~60s by a background task; a cache miss
    (e.g. right after startup) falls through to fetching all platforms
    live for this one request. See platform_service.py.

    Registered before `/{group}` below so it isn't swallowed by that
    single-segment catch-all route — FastAPI matches more specific paths
    first regardless of declaration order for path *params* vs literals,
    but keeping the literal route declared first here avoids any
    ambiguity for a human reading this file top to bottom.
    """
    return await service.get_all()


@router.get("/{group}", response_model=CommodityListOut)
async def list_group(group: str, service: CommodityService = Depends(get_commodity_service)):
    """All instruments in `group` (currently: `gold`) with full current
    data — the "get all gold ETF data in one call" endpoint. Backed by
    concurrent per-instrument fetches server-side (see service.py); from
    the caller's perspective it's exactly one request either way."""
    return await service.get_all(group)


@router.get("/{group}/changes/today", response_model=TodayChangesOut)
async def today_changes(group: str, service: CommodityService = Depends(get_commodity_service)):
    """Same underlying data as `GET /{group}`, reshaped as a
    change-of-the-day view: every instrument's change amount/percent,
    sorted biggest gainer first."""
    return await service.get_today_changes(group)


@router.get("/instrument/{ins_code}", response_model=CommodityOut)
async def get_instrument(ins_code: str, service: CommodityService = Depends(get_commodity_service)):
    return await service.get_one(ins_code)
