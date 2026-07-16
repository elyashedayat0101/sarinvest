# Architecture: current state, target structure, and the crypto domain

## 1–2. Honest assessment of the current structure

You have **horizontal layering**: one `schemas/`, one `services/`, one
`repositories/`, one `db/models/`, one `api/v1/`, each holding a file per
domain (market, portfolio, strategy) side by side. This is a completely
normal way to start a FastAPI project, and nothing about it is *wrong* —
but it has a specific failure mode as domain count grows, and you told me
you're planning to add several more (crypto now; users, payments,
weather, analytics later). Concretely:

- **Adding one domain touches 5+ directories.** Crypto's model file sits
  in `db/models/` next to `market.py` and `portfolio.py`; its schema sits
  in `schemas/` next to unrelated domains; same for `services/`,
  `repositories/`. There's no folder boundary that says "this is the
  crypto domain" — it's smeared across the tree.
- **No enforced isolation.** Nothing stops `crypto`'s service from
  importing `portfolio_repo.py` directly. That's fine with 3 domains and
  one author; it silently rots into a dependency tangle by domain 6.
- **Directory-level merge contention.** Two people (or two agents) adding
  different domains will keep touching the same shared folders.
- **A second smell, unrelated to folder layout**: one SQLite database
  *per domain* (`lotus_options.db`, `lotus_portfolio.db`). That was a
  faithful port of the original two-file Flask app, but it does not
  extend to N domains — you don't want `users.db`, `payments.db`,
  `weather.db`, each with its own engine and settings, and unable to join
  across each other when a feature needs to (e.g. "portfolio value priced
  in live crypto rates" needs both portfolio and crypto data in one
  query).

**Verdict**: restructure now, while it's 4 domains and cheap, not later
when it's 8 and expensive. The good news: your layers themselves (router
→ service → repository → SQLAlchemy models, Pydantic schemas at every
boundary) are exactly right and don't need to change — only how they're
*grouped on disk* does.

## 3. Target structure: package by feature, not by layer

```
app/
├── main.py
├── core/                     # cross-cutting only: config, logging, exceptions, DB session helpers
├── domain/                   # cross-domain pure functions (options math, alerts, jalali dates)
├── shared/                   # cross-domain reusable building blocks (cache, generic response models)
├── db/
│   ├── session.py             # generic engine/session-factory helpers (domain-agnostic)
│   └── models/base.py         # DeclarativeBase roots — see "databases" below
├── domains/
│   ├── market/                 # funds, contracts, live options data
│   ├── portfolio/              # portfolios, positions, and strategies (one bounded context — see below)
│   └── crypto/                  # exchange price tracking
│       ├── models.py
│       ├── schemas.py
│       ├── exceptions.py
│       ├── clients/             # external API integrations
│       ├── repository.py
│       ├── aggregator.py
│       ├── service.py
│       ├── tasks.py
│       ├── deps.py
│       └── router.py
```

Each domain is a **self-contained vertical slice**: its model, its
schema, its repository, its service, its router, all in one folder.
Adding domain N+1 means adding one folder, not touching five existing
ones. This is the structure every FastAPI app past ~5 domains converges
on (whatever it's called — "screaming architecture", "package by
feature", "modular monolith" — the shape is the same).

**Status: this is what the codebase actually looks like now**, not a
future plan — `market`, `portfolio` (with strategies folded in), and
`crypto` are all real packages under `app/domains/`. An earlier version
of this document proposed moving `market`/`portfolio` as a "separate,
deferred follow-up"; that turned out to be the wrong call — a codebase
mixing two structural patterns (crypto package-by-feature, everything
else horizontal-by-layer) is worse than either pattern consistently
applied, so the move happened as part of this same change instead of
being deferred. `app/schemas/`, `app/services/`, `app/repositories/`,
and `app/api/` are gone — there is nothing left in them.

**Databases**: I introduced `SharedBase` (`app/db/models/base.py`) and
one new shared SQLite database (`app_shared.db`, `settings.shared_db_url`)
that every *new* domain uses — crypto first. `market`/`portfolio` keep
their existing separate database files (`lotus_options.db`,
`lotus_portfolio.db`) — moving the code that talks to them into
`app/domains/` didn't require moving the data too, and consolidating
those onto `SharedBase` is a separate, larger decision (touches on-disk
schema, not just code layout) — see "Consolidating onto one database"
below for when that's worth doing.

## What actually moved, and what stayed put

Executed as one pass rather than a deferred follow-up (see "Status"
above for why). For the record, since it's useful context if you're
reading the git history:

1. `app/db/models/market.py` → `app/domains/market/models.py`;
   `app/schemas/market.py` → `app/domains/market/schemas.py`;
   `app/repositories/market_repo.py` → `app/domains/market/repository.py`;
   `app/services/state.py` → `app/domains/market/state.py`;
   `app/services/serializers.py` → `app/domains/market/serializers.py`;
   `app/services/fetch_service.py` + `persist_service.py` merged into
   `app/domains/market/tasks.py` (classes renamed `FetchService` →
   `FetchTask`, `PersistService` → `PersistTask`, matching the `tasks.py`
   convention crypto established); `app/api/v1/funds.py` + `health.py` +
   `market.py` merged into `app/domains/market/router.py`; the
   market-relevant half of `app/api/deps.py` → `app/domains/market/deps.py`.
2. `app/db/models/portfolio.py` → `app/domains/portfolio/models.py`;
   `app/schemas/portfolio.py` + `app/schemas/strategy.py` merged into
   `app/domains/portfolio/schemas.py`; `app/repositories/portfolio_repo.py`
   → `app/domains/portfolio/repository.py` (kept as one class — see
   "Why strategy isn't its own domain" below); `app/api/v1/portfolio.py`
   + `position.py` + `strategy.py` merged into
   `app/domains/portfolio/router.py` (as three internal `APIRouter`s
   combined into one exported `router`, preserving the original URL
   structure exactly — position endpoints are still top-level
   `/position/...`, not nested under `/portfolio/...`).
3. `app/domain/` (singular — `options_math.py`, `alerts.py`, `jalali.py`)
   stayed where it is. These are cross-domain pure-math/formatting
   utilities, not one domain's business logic — they don't belong inside
   `domains/market/` even though market currently is their only caller,
   because the next domain that needs date formatting or similar (any of
   them, eventually) shouldn't have to import from inside another
   domain's package. Living beside `shared/` rather than inside it
   already, so no move was needed there either.
4. `app/api/` is gone entirely. `main.py` now imports each domain's
   `router` directly and mounts it — the same thing an `api/v1/router.py`
   aggregator was doing, just without an extra indirection layer once
   every route belonged to some domain's own router.

### Why strategy isn't its own top-level domain

Tempting, since it's a distinct concept — but `Strategy`/`StrategyLeg`
share a foreign key straight into `Position`
(`strategy_legs.linked_position_id -> positions.id`), share the same
physical database, and the original `portfolio_db.py` already treated
both as one class's responsibility, including an atomic transaction that
creates positions *from* strategy legs in one step. Splitting them into
separate top-level domains would mean either duplicating that
transaction's logic across two repository classes, or reintroducing
cross-repository session-sharing — real complexity purchased for a
boundary that doesn't reflect how the data actually relates. "Portfolio
management" (positions + the strategies that produce them) is the
correct bounded context here; not every distinct noun needs to be its
own top-level domain.

### Consolidating onto one database (still optional, still deferred)

Moving `market`/`portfolio`'s *code* into `app/domains/` didn't move
their *data* — they're still two separate SQLite files, distinct from
`SharedBase`/`app_shared.db`. That's a genuinely separate decision from
the code-layout question this change addressed: it touches on-disk
schema and requires a real data migration (or a fresh start), not just
moving files and fixing imports. Worth doing eventually — for the same
cross-domain-JOIN reasons in the "1–2. Honest assessment" section above
— but do it deliberately, on its own, once you actually have a feature
that needs to join across them (e.g. "portfolio value priced in live
crypto rates" would need portfolio + crypto data in one query today, and
currently can't join since they're separate files).

## Where the commodities (gold/silver) domain was added, and why it's one domain

You asked directly whether gold and "other things for gold... from
another online platform" should be one domain or split up, and said not
to implement silver but to design for it. Working through that:

**Gold and silver are one domain (`commodities`), not two.** They're
structurally identical — same exchange (TSETMC), same kind of instrument
(a commodity-backed ETF), same fields, same "get all instruments in a
group" access pattern. The only thing that differs is *which instruments*
belong to which group, and that's exactly what `registry.py` encodes:
`InstrumentRef.group: Literal["gold", "silver"]`. Every method in
`service.py`/`repository.py`/`router.py` already takes `group` as a
parameter instead of hardcoding "gold" anywhere — adding silver is
*only* `registry.py::SILVER_ETF_INSTRUMENTS = [...]`, nothing else in
the domain changes. This is the same reasoning as "why strategy isn't
its own domain" above: the question isn't "are these conceptually
different things" (they are — gold and silver are different metals) but
"do they need independently-varying code," and here they don't.

**A second, different-platform gold data source is a second `client`,
not a second domain — this happened, and confirmed the shape, with a
correction.** You later supplied six actual retail gold-price platforms
(hamrahgold, digikala, technogold, talasea, milligold, melligold). The
domain call held — all six live inside `commodities/`, not a new
top-level domain — but the *client* call needed one adjustment once real
requirements showed up: these platforms return one current price per
platform (buy/sell for "gold," no per-instrument registry), which is
`crypto`'s `ExchangeClient` shape, not `CommodityDataClient`'s
("registry of instruments, fetch each by code") shape that TSETMC uses.
So they implement a new, sibling ABC —
`clients/platform_base.py::GoldPricePlatformClient` — rather than being
forced into `CommodityDataClient`. Both ABCs coexist in the same
`clients/` folder because they're genuinely different *shapes* of "fetch
external commodity data," and forcing one interface onto both would have
meant fake/unused parameters on one side or the other. The general
principle stands: let the data's actual shape decide the interface,
don't assume "same domain" implies "same ABC." See HANDOFF.md §6.5 for
the concrete result, including the partial-failure/caching pattern
these platform clients copy from `crypto.service.fetch_all`, exactly as
predicted below.

**What would actually justify a second domain**: if gold-specific
business logic showed up that has no silver equivalent — e.g., a
"physical gold backing audit" feature, or gold-specific portfolio
valuation rules distinct from a generic "commodity holding." That's a
real "this diverges, not just varies" signal, same test applied to
strategy/portfolio. Nothing like that exists yet; if it does later,
split `commodities` into `commodities` (shared fetch/price
infrastructure) + a thin `gold`-specific domain that depends on it for
prices — don't restructure preemptively for a feature that doesn't exist.

## Where the crypto domain was added

`app/domains/crypto/` — every file listed with its actual responsibility:

| File | Responsibility |
|---|---|
| `models.py` | `PriceSnapshot` SQLAlchemy model on `SharedBase`. One table for v1 — no separate rollup table yet (see file docstring for why not). |
| `schemas.py` | `RawPrice` (internal, exchange-agnostic) + API response models (`UnifiedPriceOut`, `PriceComparisonOut`, `PriceHistoryOut`, etc). Exchange-*specific* raw-response models live next to their client, not here — see "Pydantic schemas" below. |
| `exceptions.py` | `ExchangeUnavailableError`, `UnsupportedSymbolError`, `AllExchangesUnavailableError` — subclass the app's existing `LotusError` so the global handler covers them for free. |
| `clients/base.py` | `ExchangeClient` ABC: symbol-mapping, a crude local rate limit, the `fetch_price` contract every concrete client implements. |
| `clients/binance.py`, `clients/coinbase.py`, `clients/kraken.py` | One file per exchange. Each owns its base URL, its raw-response Pydantic model, and its error translation. Adding a 4th exchange = one new file here + one line in `main.py`'s client list. |
| `aggregator.py` | Pure functions (`aggregate()`): average/median/min/max/spread across a list of `RawPrice`. Zero I/O — unit-testable with plain data, no mocking. |
| `repository.py` | SQLAlchemy queries against `PriceSnapshot` — save, latest-per-exchange, history. No business logic. |
| `service.py` | Orchestration: call every exchange concurrently (`asyncio.gather`), tolerate partial failures, aggregate, cache, persist. This is the file worth reading first if you're extending this domain. |
| `tasks.py` | `CryptoPollingTask` — background loop, same shape as `market`'s `FetchTask`/`PersistTask`. |
| `deps.py` | `get_crypto_service()` — pulls the singleton off `app.state`, domain-local rather than dumped into a shared junk-drawer file. |
| `router.py` | REST endpoints, mounted at `/api/v1/crypto` (see "Versioning" below for why this one *is* versioned in the URL while the existing routes aren't). |

Wired into `main.py`'s `lifespan`: one shared `httpx.AsyncClient`, the
three exchange clients, `CryptoRepository`, `TTLCache`, the service, and
`CryptoPollingTask` — started/stopped the same way `market`'s
`FetchTask`/`PersistTask` already are. Router included in `create_app()` next to
the existing `api_router`.

**Verified, not just written**: booted the app with mocked exchange HTTP
responses (no real network access to Binance/Coinbase/Kraken from where
this was built) and exercised all 4 endpoints, including the two
failure-tolerance paths that matter most: one exchange failing while
others succeed (200, partial `sources`, populated `errors`), and every
exchange failing (503, not a silent empty success).

## 6. Best practices

### Pydantic schemas — exchange-specific vs unified

Three tiers, and the rule is: **never let one tier's shape leak into
another's file**.

1. Exchange-specific raw models (`_BinanceTicker24hr`, `_CoinbaseTicker`,
   `_KrakenResponse`) parse that exchange's actual JSON. They live in
   that exchange's client file, prefixed `_` (private to the module).
   Only fields you actually use are declared — Pydantic ignores the rest,
   so the exchange adding new fields doesn't break you.
2. `RawPrice` is the internal contract every client normalizes into.
   Never returned by an endpoint directly.
3. API response models (`UnifiedPriceOut` etc.) are what routers return —
   reshaped for the wire (ISO date strings, aggregated fields).

If you skip tier 1 and parse Binance's raw dict directly into `RawPrice`,
you've coupled your internal shape to one exchange's API — the next
exchange won't fit the same fields, and you'll be tempted to make
`RawPrice` "flexible" (optional everything), which defeats the point of
typing it at all.

### Service layer

The service is where **partial failure is a first-class outcome**, not
an edge case. `CryptoPriceService.fetch_all` uses
`asyncio.gather(..., return_exceptions=True)` and sorts results into
successes and typed errors — a dead exchange degrades the response
(fewer `sources`, populated `errors`), it doesn't 500 the endpoint. Only
raise a real HTTP error when *every* source failed
(`AllExchangesUnavailableError`). This pattern — gather-then-triage, not
gather-then-raise-on-first-failure — is the one to reuse for any future
domain that aggregates multiple external sources.

### Repository pattern

Yes, keep it, and keep it doing exactly one thing: translate between
SQLAlchemy and plain dicts/domain objects. No business logic, no
aggregation, no cross-repository calls. Each method opens its own
short-lived `AsyncSession` and commits immediately (see
`domains/market/repository.py`/`domains/portfolio/repository.py` for the
established pattern) —
don't thread a request-scoped session through repositories used by both
HTTP routes and background tasks; a per-call session is simpler and
correct for both callers.

### External API clients

- One file per external integration, one class implementing a shared
  ABC (`ExchangeClient`). This is the Strategy pattern, deliberately
  boring — resist the urge to generalize further (a generic "HTTP
  connector framework") until you have 4-5 of these and see what
  actually repeats.
- Each client owns its base URL, its pair/symbol mapping, and its error
  translation. **Never let an `httpx` exception or a raw `KeyError`
  escape a client** — translate to your domain's typed exceptions at the
  boundary, every time.
- Share one `httpx.AsyncClient` (connection pooling) across all clients
  for a given external-API family, created once in `lifespan`, closed on
  shutdown — not one client instance per call.

### Dependency injection

Two tiers — though in practice, everything so far has landed in tier 2:
- A slim, app-wide `deps.py` (doesn't exist yet — nothing has qualified)
  for dependencies genuinely shared by multiple domains: `get_settings()`
  is the only real candidate right now, and it's cheap enough
  (`@lru_cache`-backed, imported directly) that it hasn't needed a
  wrapper. Add one (e.g. `app/core/deps.py`) the moment a second domain
  needs the same non-trivial dependency.
- `app/domains/<domain>/deps.py` — everything else. `market`'s `deps.py`
  has `get_app_state`, `get_market_repo`, `get_fund_configs`,
  `get_default_fund`, `valid_fund_id`; `portfolio`'s has
  `get_portfolio_repo`; `crypto`'s has `get_crypto_service`. Each pulls
  its singleton(s) off `request.app.state`, set up once in `lifespan`.
  Cross-domain use is fine and already happens — `portfolio`'s strategy
  endpoints import `get_market_repo`/`valid_fund_id` from `market.deps`
  because they genuinely need live prices — the point isn't "domains
  never import each other," it's "no shared junk-drawer file that every
  domain has to edit."

### Versioning

Your `market`/`portfolio` routes have no version prefix in the URL
(`/api/lotus`, `/api/portfolio`, ...) — that's a "version was never
promised to clients" situation, fine for a single-frontend app, but not
something to keep doing as more domains (and possibly more API
*consumers*) show up. **Recommendation, applied here**: don't touch the
existing routes (retrofitting a version prefix onto live URLs breaks
every existing client for no functional gain) — but every *new* domain
mounts under `/api/v1/<domain>` from day one. Crypto does this
(`/api/v1/crypto/...`). When you eventually need a breaking v2 of a
specific domain, you add `/api/v2/<domain>` alongside it and deprecate
v1 on your own timeline, instead of versioning the whole app at once.

## 7. Additional recommendations

**Background tasks.** One idiom app-wide (`asyncio.Task` started/stopped
in `lifespan`, interruptible via `CancelledError`) — `CryptoPollingTask`
deliberately copies `market`'s `FetchTask`'s shape rather than inventing a
second pattern. If you eventually have 5+ of these polling loops, look at
consolidating into one generic `PollingTask(interval, fn)` utility in
`shared/` — not worth it yet at 2.

**Caching.** `shared/cache.py::TTLCache` is intentionally minimal —
in-process, no Redis. That's correct for a single instance; it stops
being correct the moment you run more than one API process, since each
process's cache is independent (fine for "smooth out a burst of
requests," wrong for anything needing cross-instance consistency, e.g. a
distributed rate-limit counter).

This is a slightly different question from "should this app use Redis at
all" — it now does, for `users`' OTP storage (`otp_store.py`), which
needed it for a different reason than caching: OTP codes are
*correctness-critical, ephemeral, TTL-native data* — every worker process
must see the same "has this code been used yet" answer, and Redis's
native key expiry replaces a cleanup job that a SQL table would need.
`crypto`'s `TTLCache` is a *performance* optimization where staleness for
a few seconds is fine and each process having its own cache is a
non-issue. Now that Redis is already a running dependency for this app,
it's a reasonable follow-up to move `TTLCache` onto it too once you
actually run more than one instance — but that's still a "when you
horizontally scale" decision, not something worth doing preemptively
just because Redis happens to be available now.

**Rate limiting.** Two layers, both present: (1) a crude per-exchange
local "don't call again within N seconds" guard in `ExchangeClient`
(`clients/base.py`) — protects the exchange from being hammered; (2) the
`TTLCache` in front of the service — protects your own endpoint from
issuing a fresh exchange call on every single frontend request. For a
single instance polling a handful of symbols, this is enough. If you add
many more tracked symbols or run multiple instances, replace the local
guard with a real token-bucket limiter (`aiolimiter`) backed by Redis
(already available — see above) so the limit is shared across processes.
The `users` domain has its own, separate rate limiting for a different
resource (OTP requests/verifies per phone number, not per exchange) —
see `service.py`'s `request_otp`/`verify_otp`.


**Testing.** Nothing exchange-specific should ever make a real HTTP
call in a test — mock `httpx.AsyncClient.get` (or use
`httpx.MockTransport`, cleaner for larger suites) and assert on
`aggregator.aggregate()` and `CryptoPriceService.fetch_all`'s
partial-failure behavior directly; that's where the actual logic worth
protecting lives. `pytest-asyncio` is already in `pyproject.toml`'s dev
deps.

**Config.** Every new domain gets its own settings block in
`app/core/config.py` (see the `# -- Crypto domain --` section) rather
than a per-domain `.env` file or a per-domain `Settings` subclass — one
place to see everything configurable, and `pydantic-settings` already
gives you env-var validation for free. If `config.py` gets unwieldy past
~10 domains, split into `CryptoSettings`/`UsersSettings`/etc. composed
into the top-level `Settings` — not needed yet.

**One naming thing worth fixing eventually**: the app's base exception
class is called `LotusError` (`app/core/exceptions.py`) — a name that
made sense when this was a single-purpose options monitor, less so now
that `crypto`'s exceptions subclass it too. Rename to `AppError` when
convenient; it's a mechanical, low-risk rename (used in `isinstance`
checks and class definitions only), just not bundled into this change.
