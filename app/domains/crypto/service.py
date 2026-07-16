"""
app/domains/crypto/service.py
================================
The orchestration layer: calls every configured exchange concurrently,
tolerates individual exchange failures (never lets one dead exchange take
down the whole response), aggregates, caches, and persists.

This is the piece most worth reading if you're extending this domain —
it's the shape every future "fetch from N external sources, unify, serve"
feature in this app should copy.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from app.domains.crypto.aggregator import aggregate
from app.domains.crypto.clients.base import ExchangeClient
from app.domains.crypto.exceptions import AllExchangesUnavailableError, ExchangeUnavailableError
from app.domains.crypto.repository import CryptoRepository
from app.domains.crypto.schemas import (
    ExchangeErrorOut, ExchangePriceOut, PriceComparisonOut, PriceHistoryOut,
    PriceHistoryPoint, RawPrice, UnifiedPriceOut,
)
from app.shared.cache import TTLCache

log = logging.getLogger("crypto.service")


class CryptoPriceService:
    def __init__(self, clients: List[ExchangeClient], repo: CryptoRepository, cache: TTLCache[UnifiedPriceOut]):
        self._clients = clients
        self._repo = repo
        self._cache = cache

    @property
    def exchange_names(self) -> List[str]:
        return [c.name for c in self._clients]

    async def fetch_all(self, symbol: str) -> Tuple[List[RawPrice], List[ExchangeErrorOut]]:
        """Call every exchange concurrently. Individual failures are
        collected, not raised — a dead exchange should degrade the
        response, not break it. Only raises if literally every exchange
        failed (see AllExchangesUnavailableError)."""
        results = await asyncio.gather(
            *(c.fetch_price(symbol) for c in self._clients), return_exceptions=True
        )

        prices: List[RawPrice] = []
        errors: List[ExchangeErrorOut] = []
        for client, result in zip(self._clients, results):
            if isinstance(result, RawPrice):
                prices.append(result)
            elif isinstance(result, ExchangeUnavailableError):
                log.warning("exchange failed: %s", result.message)
                errors.append(ExchangeErrorOut(exchange=client.name, error=result.message))
            elif isinstance(result, Exception):
                # Anything a client raises that isn't ExchangeUnavailableError
                # is a bug in that client, not an expected failure mode —
                # log it loudly rather than silently swallowing it.
                log.error("unexpected error from %s client: %s", client.name, result)
                errors.append(ExchangeErrorOut(exchange=client.name, error=str(result)))

        if not prices:
            raise AllExchangesUnavailableError(
                f"all {len(self._clients)} exchanges failed for symbol '{symbol}'"
            )
        return prices, errors

    async def get_latest_unified(self, symbol: str, use_cache: bool = True) -> UnifiedPriceOut:
        if use_cache:
            cached = await self._cache.get(symbol)
            if cached is not None:
                return cached.model_copy(update={"from_cache": True})

        prices, errors = await self.fetch_all(symbol)
        stats = aggregate(prices)
        now_iso = datetime.now(timezone.utc).isoformat()

        result = UnifiedPriceOut(
            symbol=symbol,
            average_price=stats.average_price,
            median_price=stats.median_price,
            min_price=stats.min_price,
            max_price=stats.max_price,
            spread_pct=stats.spread_pct,
            sources=[_to_out(p) for p in prices],
            errors=errors,
            fetched_at=now_iso,
            from_cache=False,
        )

        await self._cache.set(symbol, result)
        await self._repo.save_many(prices)
        return result

    async def compare(self, symbol: str) -> PriceComparisonOut:
        unified = await self.get_latest_unified(symbol, use_cache=True)
        return PriceComparisonOut(**unified.model_dump())

    async def get_history(self, symbol: str, exchange: Optional[str] = None, hours: int = 24, limit: int = 500) -> PriceHistoryOut:
        rows = await self._repo.get_history(symbol, exchange, hours, limit)
        return PriceHistoryOut(
            symbol=symbol,
            exchange=exchange,
            points=[PriceHistoryPoint(exchange=r["exchange"], price=r["price"], fetched_at=r["fetched_at"]) for r in rows],
        )


def _to_out(p: RawPrice) -> ExchangePriceOut:
    return ExchangePriceOut(
        exchange=p.exchange, symbol=p.symbol, price=p.price,
        bid=p.bid, ask=p.ask, volume_24h=p.volume_24h,
        fetched_at=p.fetched_at.isoformat(),
    )
