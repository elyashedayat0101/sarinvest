"""
app/main.py
============
Entry point. Replaces `start_server()` from the old lotus_server.py.

As of this version, every domain — `market`, `portfolio` (including
strategies), and `crypto` — lives under `app/domains/<domain>/` as a
self-contained package: its own models, schemas, repository, router, and
background tasks. `app/schemas/`, `app/services/`, `app/repositories/`,
`app/api/`, and the domain-specific files under `app/db/models/` are gone
— this file is the one place that now imports from every domain to wire
them together. See ARCHITECTURE.md for the full reasoning; this docstring
just covers what changed mechanically:

  - `db = LotusDB()` / `pdb = PortfolioDB()` are no longer *module-level*
    globals created at import time — they're created once in `lifespan`
    and attached to `app.state`, then handed out via dependency injection
    (each domain's own `deps.py`). This is what makes routers testable
    without a real sqlite file on disk.
  - Background threads (`FetchTask`, `PersistTask`, `CryptoPollingTask`)
    are started/stopped via FastAPI's `lifespan` context manager, so they
    stop cleanly on SIGTERM too, not just a Ctrl+C-style exit.
  - CORS is explicit and configurable (the original Flask app had none —
    fine only if the frontend is always same-origin; verify before
    deploying — see README_MIGRATION.md).
  - `/docs` and `/redoc` are free from the Pydantic schemas.
"""
from __future__ import annotations

import logging
import queue
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import configure_logging
from app.db.models.base import MarketBase, PortfolioBase, SharedBase
from app.db.session import create_all, make_engine, make_session_factory
from app.domains.commodities.clients.digikala import DigikalaClient
from app.domains.commodities.clients.hamrahgold import HamrahGoldClient
from app.domains.commodities.clients.melligold import MelliGoldClient
from app.domains.commodities.clients.milligold import MilliGoldClient
from app.domains.commodities.clients.talasea import TalaseaClient
from app.domains.commodities.clients.technogold import TechnoGoldClient
from app.domains.commodities.clients.tsetmc import TsetmcClient
from app.domains.commodities.platform_service import GoldPlatformPriceService
from app.domains.commodities.platform_tasks import GoldPlatformPollingTask
from app.domains.commodities.repository import CommodityRepository
from app.domains.commodities.router import router as commodities_router
from app.domains.commodities.schemas import CommodityListOut, GoldPlatformPricesOut
from app.domains.commodities.service import CommodityService
from app.domains.commodities.tasks import CommodityPollingTask
from app.domains.crypto.clients.binance import BinanceClient
from app.domains.crypto.clients.coinbase import CoinbaseClient
from app.domains.crypto.clients.kraken import KrakenClient
from app.domains.crypto.repository import CryptoRepository
from app.domains.crypto.router import router as crypto_router
from app.domains.crypto.service import CryptoPriceService
from app.domains.crypto.tasks import CryptoPollingTask
from app.domains.market.repository import MarketRepository
from app.domains.market.router import router as market_router
from app.domains.market.state import AppState
from app.domains.market.tasks import FetchTask, PersistTask
from app.domains.portfolio.repository import PortfolioRepository
from app.domains.portfolio.router import router as portfolio_router
from app.domains.users.otp_sender import LogOtpSender
from app.domains.users.otp_store import InMemoryOtpStore, OtpStore, RedisOtpStore
from app.domains.users.repository import UserRepository
from app.domains.users.router import router as users_router
from app.domains.users.service import UserService
from app.shared.cache import TTLCache
from app.shared.redis_cache import RedisCache

log = logging.getLogger("lotus.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging()

    # lotus_monitor.py and strategy_engine.py were not part of the source
    # we migrated — imported unchanged from wherever you place them
    # (legacy/). fund_config.py is real and included.
    from legacy.fund_config import FUNDS, all_fund_ids
    from legacy.lotus_monitor import Fetcher, History

    # -- Databases: one engine per domain database (market, portfolio),
    #    plus the shared database every new domain uses from crypto on --
    market_engine = make_engine(settings.lotus_db_url, echo=settings.db_echo)
    portfolio_engine = make_engine(settings.portfolio_db_url, echo=settings.db_echo)
    shared_engine = make_engine(settings.shared_db_url, echo=settings.db_echo)
    await create_all(market_engine, MarketBase)
    await create_all(portfolio_engine, PortfolioBase)
    await create_all(shared_engine, SharedBase)

    market_session_factory = make_session_factory(market_engine)
    portfolio_session_factory = make_session_factory(portfolio_engine)
    shared_session_factory = make_session_factory(shared_engine)

    # -- Shared infrastructure: Redis --
    # Originally set up just for `users`' OTP storage; `commodities`'
    # gold-platform-price cache uses the same connection now too (see
    # platform_service.py) — two real uses is what justifies this living
    # here as shared infra rather than nested inside one domain's section.
    redis_client = None
    if settings.redis_url:
        import redis.asyncio as redis_asyncio
        redis_client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
    else:
        log.warning(
            "LOTUS_REDIS_URL not set — falling back to in-process caches for "
            "OTP storage (users) and gold-platform-price caching (commodities). "
            "Fine for local dev; do NOT run this in production (breaks with >1 "
            "worker, resets on restart) — see otp_store.py and platform_service.py."
        )

    # -- Market domain --
    app_state = AppState(fund_ids=all_fund_ids(), poll_interval=settings.poll_interval)
    persist_q: "queue.Queue" = queue.Queue(maxsize=settings.persist_queue_maxsize)

    app.state.settings = settings
    app.state.fund_configs = FUNDS
    app.state.app_state = app_state
    app.state.persist_queue = persist_q
    app.state.market_repo = MarketRepository(app_state, market_session_factory)

    # -- Portfolio domain --
    app.state.portfolio_repo = PortfolioRepository(portfolio_session_factory)
    await app.state.portfolio_repo.ensure_default_portfolio()

    fetch_task = FetchTask(
        state=app_state,
        settings=settings,
        fund_configs=FUNDS,
        persist_queue=persist_q,
        fetcher_factory=lambda logger: Fetcher(logger),
        history_factory=History,
    )
    persist_task = PersistTask(persist_q, app.state.market_repo, app.state.portfolio_repo)

    # -- Crypto domain --
    # One shared httpx.AsyncClient for all exchange clients (connection
    # pooling, one place to set a global timeout) rather than one client
    # per exchange per call.
    crypto_http = httpx.AsyncClient(timeout=settings.crypto_http_timeout)
    crypto_clients: list = [
        BinanceClient(crypto_http, settings.crypto_binance_symbol_map, settings.crypto_binance_base_url, settings.crypto_min_call_interval),
        CoinbaseClient(crypto_http, settings.crypto_coinbase_symbol_map, settings.crypto_coinbase_base_url, settings.crypto_min_call_interval),
        KrakenClient(crypto_http, settings.crypto_kraken_symbol_map, settings.crypto_kraken_base_url, settings.crypto_min_call_interval),
    ]
    crypto_repo = CryptoRepository(shared_session_factory)
    crypto_cache: TTLCache = TTLCache(ttl_seconds=settings.crypto_cache_ttl)
    app.state.crypto_service = CryptoPriceService(crypto_clients, crypto_repo, crypto_cache)
    crypto_task = CryptoPollingTask(
        app.state.crypto_service, settings.crypto_tracked_symbols, settings.crypto_poll_interval
    )

    # -- Commodities domain (gold/silver ETFs via TSETMC) --
    commodity_http = httpx.AsyncClient(timeout=settings.commodity_http_timeout)
    tsetmc_client = TsetmcClient(commodity_http, max_concurrent=settings.commodity_max_concurrent_requests)
    commodity_repo = CommodityRepository(shared_session_factory)
    commodity_cache: TTLCache[CommodityListOut] = TTLCache(ttl_seconds=settings.commodity_cache_ttl)
    app.state.commodity_service = CommodityService(tsetmc_client, commodity_repo, commodity_cache)
    commodity_task = CommodityPollingTask(
        app.state.commodity_service, settings.commodity_groups, settings.commodity_poll_interval
    )

    # -- Commodities domain: gold retail-price platforms (separate from TSETMC above) --
    # Own httpx.AsyncClient — different set of hosts, different timeout
    # profile than TSETMC, no reason to share one client across both.
    gold_platform_http = httpx.AsyncClient(timeout=settings.gold_platform_http_timeout)
    gold_platform_clients: list = [
        HamrahGoldClient(gold_platform_http),
        DigikalaClient(gold_platform_http),
        TechnoGoldClient(gold_platform_http),
        TalaseaClient(gold_platform_http),
        MilliGoldClient(gold_platform_http),
        MelliGoldClient(gold_platform_http, symbol=settings.melligold_symbol),
    ]
    gold_platform_cache: object
    if redis_client is not None:
        gold_platform_cache = RedisCache(
            redis_client, namespace="gold_platform_price",
            ttl_seconds=settings.gold_platform_cache_ttl, model=GoldPlatformPricesOut,
        )
    else:
        gold_platform_cache = TTLCache(ttl_seconds=settings.gold_platform_cache_ttl)
    app.state.gold_platform_service = GoldPlatformPriceService(gold_platform_clients, gold_platform_cache)
    gold_platform_task = GoldPlatformPollingTask(
        app.state.gold_platform_service, settings.gold_platform_poll_interval
    )

    # -- Users domain --
    # LogOtpSender logs the code instead of sending an SMS — fine for
    # development, must be swapped for a real provider before shipping
    # OTP auth to real users. See otp_sender.py's docstring for how.
    if settings.env == "production":
        log.warning(
            "users domain is wired with LogOtpSender in a production env — "
            "OTP codes are being logged, not sent. Replace before going live."
        )

    otp_store: OtpStore = RedisOtpStore(redis_client) if redis_client is not None else InMemoryOtpStore()

    user_repo = UserRepository(shared_session_factory)
    app.state.user_service = UserService(user_repo, LogOtpSender(), otp_store, settings)

    fetch_task.start()
    persist_task.start()
    crypto_task.start()
    commodity_task.start()
    gold_platform_task.start()
    log.info("background services started, waiting for first fetch...")

    got_data = await fetch_task.wait_for_first_fetch(settings.first_fetch_timeout)
    if got_data:
        total = sum(len(fs.contracts) for fs in app_state.funds.values())
        log.info("first fetch complete — %s contracts across %s funds", total, len(FUNDS))
    else:
        log.warning("first fetch timed out after %ss — serving anyway", settings.first_fetch_timeout)

    try:
        yield
    finally:
        log.info("shutting down background services...")
        await fetch_task.stop()
        await persist_task.stop()
        await crypto_task.stop()
        await crypto_http.aclose()
        await commodity_task.stop()
        await commodity_http.aclose()
        await gold_platform_task.stop()
        await gold_platform_http.aclose()
        if redis_client is not None:
            await redis_client.aclose()
        # dispose the SQLAlchemy connection pools cleanly — new vs. the
        # sqlite3 version, where every call opened/closed its own
        # connection so there was no pool to tear down
        await market_engine.dispose()
        await portfolio_engine.dispose()
        await shared_engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="Multi-fund Iranian options monitor, strategy builder, and crypto price tracker API",
        version="3.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    # market/portfolio keep their original unversioned URLs (/api/...);
    # crypto (and every domain added after it) mounts its own full prefix
    # (/api/v1/crypto/...) — see ARCHITECTURE.md's versioning section.
    app.include_router(market_router, prefix="/api")
    app.include_router(portfolio_router, prefix="/api")
    app.include_router(crypto_router)
    app.include_router(commodities_router)
    app.include_router(users_router)

    static_dir = Path(settings.static_dir)
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    avatar_dir = Path(settings.avatar_upload_dir)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/avatars", StaticFiles(directory=str(avatar_dir)), name="avatars")

    index_path = Path(settings.index_file)

    @app.get("/", include_in_schema=False)
    async def index():
        from fastapi.responses import FileResponse
        return FileResponse(index_path) if index_path.exists() else {"service": settings.app_name}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,  # avoid a second set of background tasks spinning up in a reloader subprocess
    )
