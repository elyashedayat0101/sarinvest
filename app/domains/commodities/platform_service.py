"""
app/domains/commodities/platform_service.py
================================================
Separate file from `service.py` (TSETMC ETF tracking) on purpose — same
domain, genuinely different concern (retail/physical gold price
aggregation vs. exchange-traded fund data), different data shape,
different external sources. Keeping them in one file would just make
that file's "what does this actually do" question harder to answer.

Same gather-then-triage partial-failure pattern as
`crypto.service.CryptoPriceService.fetch_all` — one platform being down
degrades the response, it doesn't fail the whole request.

Cache is injected as either `RedisCache` or `shared.cache.TTLCache` —
this class doesn't care which, both expose the same `get`/`set` shape.
`main.py` decides which one to construct based on whether
`LOTUS_REDIS_URL` is set, same dev/prod split as `users`' OTP store.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Tuple, Union

from app.domains.commodities.clients.platform_base import GoldPricePlatformClient
from app.domains.commodities.exceptions import AllGoldPlatformsUnavailableError, GoldPlatformUnavailableError
from app.domains.commodities.schemas import GoldPlatformErrorOut, GoldPlatformPriceOut, GoldPlatformPricesOut, RawGoldPlatformPrice
from app.shared.cache import TTLCache
from app.shared.redis_cache import RedisCache

log = logging.getLogger("commodities.platform_service")

_CACHE_KEY = "current"  # single key — there's only ever one "current all-platforms" snapshot


class GoldPlatformPriceService:
    def __init__(
        self,
        clients: List[GoldPricePlatformClient],
        cache: Union[RedisCache[GoldPlatformPricesOut], "TTLCache[GoldPlatformPricesOut]"],
    ):
        self._clients = clients
        self._cache = cache

    @property
    def platform_names(self) -> List[str]:
        return [c.name for c in self._clients]

    async def fetch_all(self) -> Tuple[List[RawGoldPlatformPrice], List[Tuple[str, str]]]:
        results = await asyncio.gather(
            *(c.fetch_price() for c in self._clients), return_exceptions=True
        )
        successes: List[RawGoldPlatformPrice] = []
        errors: List[Tuple[str, str]] = []
        for client, result in zip(self._clients, results):
            if isinstance(result, RawGoldPlatformPrice):
                successes.append(result)
            elif isinstance(result, GoldPlatformUnavailableError):
                log.warning("platform failed: %s", result.message)
                errors.append((client.name, result.message))
            elif isinstance(result, Exception):
                log.error("unexpected error from %s client: %s", client.name, result)
                errors.append((client.name, str(result)))
        return successes, errors

    async def get_all(self, use_cache: bool = True) -> GoldPlatformPricesOut:
        if use_cache:
            cached = await self._cache.get(_CACHE_KEY)
            if cached is not None:
                return cached.model_copy(update={"from_cache": True})

        successes, errors = await self.fetch_all()
        if not successes:
            raise AllGoldPlatformsUnavailableError(
                f"all {len(self._clients)} gold price platforms failed"
            )

        result = GoldPlatformPricesOut(
            platforms=[_to_out(p) for p in successes],
            errors=[GoldPlatformErrorOut(platform=name, error=msg) for name, msg in errors],
            fetched_at=datetime.now(timezone.utc).isoformat(),
            from_cache=False,
        )
        await self._cache.set(_CACHE_KEY, result)
        return result


def _to_out(p: RawGoldPlatformPrice) -> GoldPlatformPriceOut:
    return GoldPlatformPriceOut(
        platform=p.platform, buy_price=p.buy_price, sell_price=p.sell_price,
        unit=p.unit, currency=p.currency, fetched_at=p.fetched_at.isoformat(),
    )
