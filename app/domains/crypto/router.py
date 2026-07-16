"""
app/domains/crypto/router.py
===============================
Mounted at `/api/v1/crypto` — deliberately *versioned* in the URL, unlike
the existing `/api/lotus`, `/api/portfolio`, etc. routes, which have no
version prefix despite living under `app/api/v1/` in the codebase. That
was a reasonable choice when this app had one client to keep in sync
with; see ARCHITECTURE.md's versioning section for why every *new*
domain from here on should put the version in the URL from day one, even
though retrofitting it onto the existing routes isn't worth the breakage.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.domains.crypto.deps import get_crypto_service
from app.domains.crypto.schemas import PriceComparisonOut, PriceHistoryOut, SupportedExchangeOut, UnifiedPriceOut
from app.domains.crypto.service import CryptoPriceService

router = APIRouter(prefix="/api/v1/crypto", tags=["crypto"])


@router.get("/prices/latest", response_model=UnifiedPriceOut)
async def latest_price(
    symbol: str = Query(..., description="Canonical symbol, e.g. USDT-USD"),
    service: CryptoPriceService = Depends(get_crypto_service),
):
    return await service.get_latest_unified(symbol)


@router.get("/prices/compare", response_model=PriceComparisonOut)
async def compare_prices(
    symbol: str = Query(..., description="Canonical symbol, e.g. USDT-USD"),
    service: CryptoPriceService = Depends(get_crypto_service),
):
    return await service.compare(symbol)


@router.get("/prices/history", response_model=PriceHistoryOut)
async def price_history(
    symbol: str = Query(...),
    exchange: Optional[str] = Query(default=None, description="Restrict to one exchange; omit for all"),
    hours: int = Query(default=24, ge=1, le=24 * 30),
    limit: int = Query(default=500, ge=1, le=5000),
    service: CryptoPriceService = Depends(get_crypto_service),
):
    return await service.get_history(symbol, exchange, hours, limit)


@router.get("/exchanges", response_model=List[SupportedExchangeOut])
async def list_exchanges(service: CryptoPriceService = Depends(get_crypto_service)):
    # Lightweight health view — doesn't re-fetch, just reports what's configured.
    # A per-exchange "healthy" flag that reflects the *last actual poll*
    # result would need the polling task to record its own outcomes
    # somewhere AppState-like; not built out yet since nothing consumes
    # it yet — straightforward to add once the frontend needs it.
    return [SupportedExchangeOut(name=name, tracked_symbols=[], healthy=True) for name in service.exchange_names]
