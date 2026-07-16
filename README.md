# Lotus Options Monitor — FastAPI edition

Migrated from a single-file Flask app (`lotus_server.py`) to a FastAPI
project organized **by domain**, not by technical layer. See
**ARCHITECTURE.md** for the structural reasoning and the best-practices
guide; **README_MIGRATION.md** for the original Flask→FastAPI and
raw-SQL→SQLAlchemy migration details; **legacy/README.md** for which of
your existing files to drop in before this will actually run.

```
app/
├── main.py                # app factory, lifespan (startup/shutdown), wires every domain together
├── core/
│   ├── config.py            # pydantic-settings — all env-driven config, one block per domain
│   ├── logging_config.py
│   └── exceptions.py        # base AppError-style exception + global handler, shared by every domain
├── domain/                   # cross-domain pure functions, zero framework/db imports
│   ├── options_math.py       # delta/theta/max-pain
│   ├── alerts.py             # Persian alert generation
│   └── jalali.py             # Jalali calendar conversion
├── shared/                   # cross-domain reusable building blocks
│   ├── cache.py                # generic in-memory TTL cache
│   └── redis_cache.py           # generic Redis-backed cache (Pydantic model + TTL) — used by commodities' gold-platform feature
├── db/
│   ├── session.py            # generic SQLAlchemy async engine + session-factory helpers
│   ├── utils.py                # ORM row -> plain dict helper
│   └── models/
│       └── base.py             # MarketBase / PortfolioBase / SharedBase — see ARCHITECTURE.md
│
└── domains/                  # every domain lives here — package by feature, not by layer
    ├── market/                  # funds, live contracts, options chain
    │   ├── models.py              # ContractRecord/Snapshot/Trade/DailyOHLC/AlertLog (on MarketBase)
    │   ├── schemas.py             # Pydantic v2 request/response models
    │   ├── state.py               # AppState — in-memory live market state
    │   ├── repository.py          # SQLAlchemy queries
    │   ├── serializers.py         # contract_to_api / build_analysis / build_insights
    │   ├── tasks.py               # FetchTask + PersistTask (background polling/persistence)
    │   ├── deps.py                # domain-local DI (get_market_repo, valid_fund_id, ...)
    │   └── router.py              # /api/lotus, /api/funds, /api/health, /api/contract/...
    │
    ├── portfolio/                # portfolios, positions, and strategies (one bounded context)
    │   ├── models.py               # Portfolio/Position/MarketCache/Strategy/StrategyLeg/etc (on PortfolioBase)
    │   ├── schemas.py              # portfolio + strategy request/response models
    │   ├── repository.py           # SQLAlchemy queries — one class, see ARCHITECTURE.md for why
    │   ├── deps.py                 # get_portfolio_repo
    │   └── router.py               # /api/portfolio/..., /api/position/..., /api/strategy/...
    │
    ├── crypto/                   # exchange price tracking
    │   ├── models.py               # PriceSnapshot (on SharedBase)
    │   ├── schemas.py              # RawPrice (internal) + API response models
    │   ├── exceptions.py
    │   ├── clients/                  # one file per exchange
    │   │   ├── base.py
    │   │   ├── binance.py
    │   │   ├── coinbase.py
    │   │   └── kraken.py
    │   ├── aggregator.py            # pure avg/median/spread math
    │   ├── repository.py            # SQLAlchemy queries
    │   ├── service.py               # orchestration + caching + partial-failure handling
    │   ├── tasks.py                 # background polling loop
    │   ├── deps.py                  # get_crypto_service
    │   └── router.py                # /api/v1/crypto/...
    │
    ├── commodities/               # gold (and, later, silver) price tracking — TWO data concepts, see below
    │   ├── models.py                # CommodityPriceSnapshot (on SharedBase) — TSETMC ETF snapshots
    │   ├── registry.py              # known TSETMC instruments, keyed by group — gold populated, silver ready
    │   ├── schemas.py                # RawCommodityPrice + RawGoldPlatformPrice (internal) + all API models
    │   ├── exceptions.py
    │   ├── clients/
    │   │   ├── base.py                 # CommodityDataClient ABC (TSETMC-style: registry -> per-instrument)
    │   │   ├── tsetmc.py                # TSETMC client (own httpx calls, not the tsetmc PyPI package — see file docstring)
    │   │   ├── platform_base.py          # GoldPricePlatformClient ABC (crypto-style: N sources -> one current price) + shared defensive-parsing helpers
    │   │   ├── hamrahgold.py               # one file per retail platform — all SIX response shapes UNVERIFIED, see platform_base.py
    │   │   ├── digikala.py
    │   │   ├── technogold.py
    │   │   ├── talasea.py
    │   │   ├── milligold.py
    │   │   └── melligold.py
    │   ├── repository.py              # SQLAlchemy queries (TSETMC snapshots only — platform prices aren't SQL, see below)
    │   ├── service.py                 # TSETMC orchestration + caching + today's-change view
    │   ├── platform_service.py         # gold-platform orchestration — Redis-or-in-memory cache, partial-failure tolerance
    │   ├── tasks.py                   # TSETMC background polling loop
    │   ├── platform_tasks.py           # gold-platform background polling loop (default: every 60s)
    │   ├── deps.py                    # get_commodity_service, get_gold_platform_service
    │   └── router.py                  # /api/v1/commodities/{group}, /{group}/changes/today, /instrument/{ins_code}, /gold/platforms
    │
    └── users/                    # accounts, OTP auth, profiles, admin actions
        ├── models.py                # User, RefreshToken (on SharedBase — no OTP table, see below)
        ├── schemas.py               # auth flow / profile / admin request-response models
        ├── security.py              # JWT issuance/verification, OTP hashing, phone normalization
        ├── otp_sender.py            # OTP delivery abstraction (dev: logs the code; swap in a real SMS provider)
        ├── otp_store.py             # OTP storage — Redis-backed (TTL-native), in-memory dev fallback
        ├── repository.py            # SQLAlchemy queries (users/refresh_tokens only — OTP isn't SQL, see otp_store.py)
        ├── service.py               # OTP + JWT lifecycle, profile management, admin actions
        ├── deps.py                  # get_current_user, require_admin
        └── router.py                # /api/v1/users/auth/..., /me/..., /admin/...

legacy/                        # only fund_config.py (real) + lotus_monitor.py,
                                # strategy_engine.py (still needed from you) — see legacy/README.md
```

**Note on URLs**: `market`/`portfolio` keep their original unversioned
paths (`/api/lotus`, `/api/portfolio/...`) — that's deliberate, not an
inconsistency; see ARCHITECTURE.md's versioning section. `crypto`,
`users`, and every domain added after `crypto` mounts under
`/api/v1/<domain>` instead.

**Note on the `users` domain**: needs `LOTUS_JWT_SECRET_KEY` and
`LOTUS_OTP_HASH_SECRET` set to real secrets (not the obviously-insecure
dev defaults) before it's safe to expose, and `LOTUS_REDIS_URL` pointed
at a real Redis instance before it's safe to run with more than one
worker process. Local dev without Redis works (falls back to an
in-memory OTP store, logged as a warning on startup) but that fallback
must never reach production — see `app/domains/users/otp_store.py`.
Quickest way to get a local Redis for development: `docker run -p
6379:6379 redis:7-alpine`.

**Note on the `commodities` domain (TSETMC part)**: talks directly to
`cdn.tsetmc.com` (TSETMC's real API, reverse-engineered from
github.com/5j9/tsetmc's source rather than depended on as a package —
see `clients/tsetmc.py`'s docstring for why). Per-instrument fields
(`Instrument/GetInstrumentInfo`, `ClosingPrice/GetClosingPriceInfo`,
`Fund/GetETFByInsCode`) are verified against that package's typed
response models. The bulk "all commodity funds in one call" endpoint
(`ClosingPrice/GetTradeTop/CommodityFund`) is implemented but its exact
field names were inferred, not independently observed — see
`schemas.py`'s docstring before relying on it in production. The known
instrument list lives in `registry.py`; adding silver is "append entries
with `group='silver'`", nothing else changes.

**Note on the `commodities` domain (gold retail-price platforms part) —
READ BEFORE DEPLOYING**: `GET /api/v1/commodities/gold/platforms`
aggregates six external platforms (hamrahgold, digikala, technogold,
talasea, milligold, melligold). Unlike TSETMC above, **none of these six
response shapes could be verified** — every one blocks automated
fetching via `robots.txt`, and none publish public API docs. The URLs
and query params are used exactly as supplied; the JSON field-parsing in
each `clients/*.py` file is a best-effort guess with a diagnostic
fallback (a failed parse raises an error that includes the real response's
top-level keys, so fixing it is "read the error, change one line," not
"start over"). Hit each URL directly with real credentials/browser
before trusting this in production — see
`clients/platform_base.py`'s module docstring for the full explanation
and the exact follow-up steps.

**Read `ARCHITECTURE.md` first** if you're adding a new domain — it has
the full structural analysis, the reasoning behind every choice above,
and a worked example (`crypto`) to copy the shape of.

Quick start: see the "Running it" section in `README_MIGRATION.md`.
