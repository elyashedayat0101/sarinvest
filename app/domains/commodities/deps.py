"""
app/domains/commodities/deps.py
==================================
Domain-local DI, same pattern as every other domain.
"""
from __future__ import annotations

from fastapi import Request

from app.domains.commodities.platform_service import GoldPlatformPriceService
from app.domains.commodities.service import CommodityService


def get_commodity_service(request: Request) -> CommodityService:
    return request.app.state.commodity_service


def get_gold_platform_service(request: Request) -> GoldPlatformPriceService:
    return request.app.state.gold_platform_service
