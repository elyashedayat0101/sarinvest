# PROJECT HANDOFF — Lotus Options Monitor / FastAPI Multi-Domain Backend

**Read this document first, in full, before touching any code.** It's
written so an AI or developer with zero prior context on this
conversation can pick up exactly where it left off. Where this document
and the in-repo docs (`README.md`, `ARCHITECTURE.md`,
`README_MIGRATION.md`, `legacy/README.md`) overlap, this document is the
up-to-date index — go to the linked doc for depth, not for "which one is
current."

---

## 1. What this project is

A FastAPI backend that started as a single-file Flask app
(`lotus_server.py`) monitoring Iranian gold-fund options (IME exchange),
and has grown into a multi-domain platform. Five domains exist today:

| Domain | What it does | Status |
|---|---|---|
| `market` | Live options chain for Iranian gold-fund options (IME), background-polled | Fully working, migrated from original Flask app |
| `portfolio` | User portfolios, positions, options strategies | Fully working, migrated from original Flask app |
| `crypto` | Multi-exchange crypto price tracking (Binance/Coinbase/Kraken) | Fully working, built new |
| `users` | Phone+OTP auth, JWT access/refresh tokens, profiles, admin actions | Fully working, built new |
| `commodities` | TSETMC gold-ETF price tracking (32 instruments), silver-ready | Fully working, built new |

Everything has been **actually run and smoke-tested** in this
environment (mocked external HTTP where a real network target wasn't
reachable — see §7). Nothing here is untested scaffolding.

The single most important structural fact: **this codebase is organized
by domain, not by technical layer.** Every domain lives at
`app/domains/<name>/` and is self-contained (its own models, schemas,
repository, router, background tasks). There is no `app/schemas/`,
`app/services/`, `app/repositories/`, or `app/api/` anymore — those
existed early in this project's history and were fully migrated away.
**If you are about to create a file in a shared top-level folder like
that, stop — it almost certainly belongs inside a domain package
instead.** See `ARCHITECTURE.md` for the full reasoning; it's required
reading before adding a sixth domain.

---

## 2. Document map

- **This file** — index, current state, what's left to do.
- **`README.md`** — file-by-file structure diagram, quick-start commands.
- **`ARCHITECTURE.md`** — *why* the codebase is shaped this way: the
  package-by-feature decision, the market/portfolio migration into that
  shape, the crypto and commodities domain-boundary reasoning, and a
  full best-practices section (schemas, service layer, repositories,
  external API clients, DI, versioning, caching, rate limiting).
  **Read this before adding a new domain or arguing about where
  something belongs.**
- **`README_MIGRATION.md`** — historical record of the Flask→FastAPI and
  raw-SQL→SQLAlchemy migrations specifically: what changed, bugs found
  and fixed, why. File paths in it predate the domain restructuring
  (there's a note at the top of that file saying so).
- **`legacy/README.md`** — which files under `legacy/` are real
  (`fund_config.py`) vs. still needed from the person running this
  (`lotus_monitor.py`, `strategy_engine.py`) vs. deleted because
  superseded (`lotus_db.py`, `portfolio_db.py` — fully replaced by
  SQLAlchemy models in `app/domains/market/` and `app/domains/portfolio/`).

---

## 3. What's real vs. what's still a stub

This matters more than anything else in this document for "can I run
this right now."

**Real, provided by the project owner, fully wired in:**
- `legacy/fund_config.py` — real fund registry for the `market` domain.
- Every file under `app/` — all real, all working code (not scaffolding).

**Still needed from the project owner — genuinely not provided, and this
codebase cannot run without them:**
- `legacy/lotus_monitor.py` — must export `Fetcher`, `Contract`, `Config`,
  `History`, `estimate_spot`, `implied_vol`, `norm_cdf`, `make_logger`.
  This is the actual IME options data fetcher; nothing about its real
  implementation is known. All code calling into it was written to match
  the *original Flask app's* call sites exactly (same function names,
  same argument order) — so if the real file matches that shape, it
  should just work, but this has never been verified against real
  code, only against hand-written stubs (see §7).
- `legacy/strategy_engine.py` — must export `evaluate_strategy`,
  `net_cost`, `compute_payoff_curve`, `compute_bounds_and_breakevens`,
  `probability_of_profit`, `suggest_strategies`, and the `template_*`
  functions. Same caveat as above.
- `index1.html` + a `static/` folder, if the original Flask app's
  frontend is still wanted — the FastAPI app serves them from `/` and
  `/static` if present, but doesn't require them (falls back to a JSON
  service banner at `/`).

**Explicitly dev-only, must be swapped before production:**
- `app/domains/users/otp_sender.py::LogOtpSender` — logs OTP codes
  instead of sending SMS. Wired in `main.py` unconditionally today, with
  only a startup warning if `settings.env == "production"`. A real
  provider (Kavenegar, Twilio, etc.) needs to be written as a sibling
  class implementing `OtpSender` and swapped in.
- `app/domains/users/otp_store.py::InMemoryOtpStore` — used automatically
  when `LOTUS_REDIS_URL` is unset. Breaks with more than one worker
  process, resets on restart. Set `LOTUS_REDIS_URL` to use
  `RedisOtpStore` instead (same interface, just needs a running Redis).
- `settings.jwt_secret_key` and `settings.otp_hash_secret` — ship with
  obviously-fake defaults (`"CHANGE_ME_dev_only_..."`) specifically so a
  deployment that forgets to override them is easy to notice.

**Explicitly unverified (documented, not hidden):**
- `app/domains/commodities/clients/tsetmc.py::fetch_bulk_commodity_funds`
  — the single-HTTP-call "all commodity funds at once" endpoint. Field
  names were inferred from TSETMC's general JSON conventions, not
  independently observed (no network path to `tsetmc.com` from the
  environment this was built in). The per-instrument path (used by
  default everywhere) *is* verified — see §6.
- **All six gold retail-price platform clients**
  (`app/domains/commodities/clients/{hamrahgold,digikala,technogold,talasea,milligold,melligold}.py`)
  — every one blocks automated fetching (`robots.txt`) and none publish
  public API docs, so *none* of their JSON response shapes could be
  confirmed (unlike TSETMC, where a real typed reference library
  existed). URLs/params are exact, as supplied. Field-name parsing is a
  best-effort guess with a diagnostic fallback — a failed parse raises
  an error containing the real response's top-level keys, so fixing a
  wrong guess is fast. **This is the single largest unverified surface
  in the whole project — hit each URL directly before deploying
  `GET /api/v1/commodities/gold/platforms`.** See
  `clients/platform_base.py`'s module docstring.
- Two rows in `app/domains/commodities/registry.py` share the same ISIN
  (`IRTKROZG0001`, two different `insCode`s) — kept as given by the
  project owner, flagged with a comment, not deduplicated. Worth
  confirming with them whether that's intentional.

---

## 4. Full directory reference

```
app/
├── main.py                 # app factory + lifespan — wires every domain together; READ THIS to
│                            #   understand how any given piece gets constructed and injected
├── core/
│   ├── config.py             # ALL settings, one field per line, grouped by domain with comments
│   ├── logging_config.py
│   └── exceptions.py         # LotusError (base for every domain's exceptions) + global handler
├── domain/                  # cross-domain pure functions — no framework/db imports, ever
│   ├── options_math.py        # delta/theta/max-pain (market domain's math)
│   ├── alerts.py               # Persian alert generation (market domain's alerts)
│   └── jalali.py                # Jalali calendar conversion (portfolio domain's dates)
├── shared/                  # cross-domain reusable infrastructure
│   └── cache.py                # generic in-memory TTL cache (used by crypto, commodities)
├── db/
│   ├── session.py             # generic SQLAlchemy async engine + session-factory helpers
│   ├── utils.py                 # model_to_dict() — ORM row -> plain dict
│   └── models/base.py           # MarketBase / PortfolioBase / SharedBase (see §5)
└── domains/
    ├── market/       — see §6.1
    ├── portfolio/     — see §6.2
    ├── crypto/         — see §6.3
    ├── users/           — see §6.4
    └── commodities/      — see §6.5

legacy/                   # external modules — see §3
```

Every domain package follows the same internal shape (not all files
apply to every domain — only build what a domain actually needs):
`models.py`, `schemas.py`, `exceptions.py`, `repository.py`,
`service.py`, `tasks.py`, `deps.py`, `router.py`, plus a `clients/`
subpackage for domains that talk to external APIs.

---

## 5. Databases

Three SQLite files, via SQLAlchemy async (`aiosqlite` driver):

| File (default path) | `DeclarativeBase` | Domains using it |
|---|---|---|
| `lotus_options.db` | `MarketBase` | `market` only |
| `lotus_portfolio.db` | `PortfolioBase` | `portfolio` only |
| `app_shared.db` | `SharedBase` | `crypto`, `users`, `commodities` — every domain added after the first two |

**Why three files, not one**: `market`/`portfolio` kept their original
two-file split from the pre-FastAPI Flask app (migrating the *code* into
`app/domains/` didn't require migrating the *data*). Every domain added
since has used one shared database instead of getting its own file —
see `ARCHITECTURE.md`'s "databases" section for the full reasoning and
for when consolidating everything onto one database (ideally Postgres)
would be worth doing.

Full table inventory:

| Table | Domain | Notes |
|---|---|---|
| `contracts`, `snapshots`, `trades`, `daily_ohlc`, `alerts_log` | market | |
| `portfolios`, `positions`, `market_cache`, `position_pnl`, `position_notes`, `strategies`, `strategy_legs` | portfolio | `position_pnl` is defined but nothing currently writes to it (ported as-is from the original schema) |
| `crypto_price_snapshots` | crypto | |
| `users`, `refresh_tokens` | users | **no OTP table** — OTP codes live in Redis/in-memory (`otp_store.py`), not SQL, deliberately |
| `commodity_price_snapshots` | commodities | TSETMC ETF data only — gold *platform* prices (hamrahgold etc.) are **not persisted to SQL at all**, only cached in Redis/in-memory (`platform_service.py`); there's no historical table for them today |

All tables use `SharedBase`/`MarketBase`/`PortfolioBase.metadata.create_all`
at startup (`CREATE TABLE IF NOT EXISTS` semantics) — safe to run against
an existing database with data in it.

---

## 6. Domain reference

### 6.1 `market` — IME options chain

**Purpose**: background-polls the IME options feed via `legacy/lotus_monitor.py`
(external, see §3), computes Greeks/max-pain/alerts, serves live state.

**Key files**: `state.py` (`AppState` — in-memory live data, `threading.Lock`
because the writer is a background OS thread, not an asyncio task —
see the long comment at the top of that file for why `asyncio.Lock`
would be wrong here), `tasks.py` (`FetchTask` + `PersistTask`),
`serializers.py`, `repository.py`, `deps.py` (`valid_fund_id` — used by
`portfolio`'s strategy endpoints too, a deliberate cross-domain import).

**Routes** (mounted unprefixed at `/api/...` — see §8 for why):
`GET /api/health`, `GET /api/funds`, `GET /api/lotus`,
`GET /api/contracts/available`, `GET /api/contract/{code}`,
`GET /api/contract/{code}/trades`, `GET /api/contract/{code}/intraday`,
`GET /api/contract/{code}/ohlc`, `GET /api/alerts`.

**Exceptions**: `FundNotFoundError`, `MarketDataUnavailableError` (both
in `app/core/exceptions.py` — predate the domain split, still used here).

### 6.2 `portfolio` — portfolios, positions, strategies

**Purpose**: user portfolio/position CRUD with live P&L, plus options
strategy building — folded into one domain deliberately (see
`ARCHITECTURE.md`'s "why strategy isn't its own domain").

**Key files**: `repository.py` (one class, ~450 lines, covers both
portfolio/position and strategy persistence — `convert_strategy_to_positions`
runs as one atomic transaction, a deliberate improvement over the
original Flask app's behavior, documented in this file's docstring).

**Routes** (unprefixed `/api/...`): `GET/POST /api/portfolio`,
`DELETE /api/portfolio/{pid}`, `GET /api/portfolio/{pid}/summary`,
`POST /api/portfolio/{pid}/position`, `GET /api/portfolio/{pid}/strategies`,
`GET/POST/DELETE /api/position/{pos_id}` + `/close` + `/note`,
`GET /api/strategy/suggest`, `POST /api/strategy/calculate`,
`POST /api/strategy/simulate`, `POST /api/strategy`,
`GET/DELETE /api/strategy/{sid}`, `POST /api/strategy/{sid}/convert`,
`GET /api/strategy/template/{strategy_type}`.

**Depends on `market`**: `suggest`/`template` endpoints need live prices
— imports `market.deps.get_market_repo`/`valid_fund_id` directly.

**Exceptions**: `PositionNotFoundError`, `StrategyNotFoundError`,
`InvalidStrategyTypeError`, `InvalidRequestError` (in
`app/core/exceptions.py`).

**Still external**: `legacy/strategy_engine.py` (see §3).

### 6.3 `crypto` — multi-exchange price tracking

**Purpose**: tracks a symbol (default `USDT-USD`) across Binance,
Coinbase, Kraken concurrently, tolerating partial exchange failure.

**Key files**: `clients/base.py` (`ExchangeClient` ABC — the pattern
every other "talk to N external data sources" domain in this codebase
copies), `clients/{binance,coinbase,kraken}.py`, `aggregator.py` (pure
avg/median/spread math), `service.py` (`fetch_all` — the
gather-then-triage partial-failure pattern to copy elsewhere).

**Routes** (versioned — `/api/v1/crypto/...`): `GET /prices/latest`,
`GET /prices/compare`, `GET /prices/history`, `GET /exchanges`.

**Known gap**: `settings.crypto_binance_symbol_map` ships **empty** —
Binance's main exchange has no direct USDT/USD spot pair; see the long
comment in `config.py` for the three real options (Binance.US, a proxy
pair, or dropping Binance for this symbol). Coinbase/Kraken are mapped
and working.

**Exceptions**: `ExchangeUnavailableError`, `UnsupportedSymbolError`,
`AllExchangesUnavailableError`.

### 6.4 `users` — phone+OTP auth, profiles, admin

**Purpose**: passwordless auth (phone number + OTP, no passwords
anywhere in this app), JWT access/refresh with rotation, profile
management (username/avatar/bio), role-based admin actions.

**Key files**: `security.py` (JWT issue/verify, OTP hashing — read the
docstring for *why* OTP codes are hashed with plain sha256+pepper
instead of bcrypt, a deliberate choice not an oversight), `otp_store.py`
(Redis-backed OTP storage — **not a SQL table**, see §5), `otp_sender.py`
(dev-only log sender, see §3), `service.py` (one class covering both
auth and profile — see file docstring for why it's not split).

**Routes** (versioned — `/api/v1/users/...`):
- Auth: `POST /auth/otp/request`, `POST /auth/otp/verify`,
  `POST /auth/refresh`, `POST /auth/logout`.
- Profile: `GET/PATCH /me`, `POST /me/avatar`, `GET /username-available`.
- Admin (all require `role="admin"`): `GET /admin/users`,
  `GET/PATCH /admin/users/{user_id}`.

**Auth mechanics worth knowing**:
- Refresh tokens **rotate on every use** — old token is revoked the
  moment a new one is issued. Presenting an already-revoked refresh
  token triggers a **cascade revocation of every session for that user**
  (stolen-token-reuse detection).
- Access tokens are semi-stateless: JWT-verified, but `get_current_user`
  still does one DB lookup per request (to catch deactivation/role
  changes immediately rather than waiting for token expiry) — a
  documented tradeoff in `service.py`, not an oversight.
- First admin account: `settings.bootstrap_admin_phone_numbers` — any
  phone number in that list gets `role="admin"` automatically on first
  login (and promoted on next login if added to the list after the
  account already existed — promote-only, never auto-demotes).
- Admin self-demotion is blocked (`SelfRoleChangeForbiddenError`) —
  can't accidentally lock yourself out via the admin API.

**Exceptions**: 11 total, all in `app/domains/users/exceptions.py` —
`InvalidPhoneNumberError`, `OtpInvalidError`, `OtpRequestRateLimitedError`,
`OtpVerifyRateLimitedError`, `UsernameTakenError`, `UserNotFoundError`,
`UserInactiveError`, `InvalidTokenError`, `AdminRequiredError`,
`SelfRoleChangeForbiddenError`, `AvatarUploadError`.

### 6.5 `commodities` — TSETMC gold ETF tracking

**Purpose**: tracks 32 known gold-backed ETFs on TSETMC (Tehran Stock
Exchange), fetching identity/price/NAV/change data concurrently.
Structurally ready for silver (and other groups) with zero code changes
beyond populating a list — see `ARCHITECTURE.md`'s dedicated section on
this domain's boundary reasoning.

**Key files**: `registry.py` (the known-instrument list — `GOLD_ETF_INSTRUMENTS`
populated, `SILVER_ETF_INSTRUMENTS` empty and ready),
`clients/tsetmc.py` (real TSETMC API calls via `httpx` directly — **not**
a dependency on the `tsetmc` PyPI package; see that file's docstring for
why, including a real compatibility problem with that package on Python
3.12).

**Routes** (versioned — `/api/v1/commodities/...`):
`GET /{group}` (e.g. `/gold` — all instruments in that group, full data,
in one response), `GET /{group}/changes/today` (same data, sorted by
`change_percent` descending), `GET /instrument/{ins_code}`.

**Data provenance** (important — see §3's "unverified" note):
per-instrument fields (`Instrument/GetInstrumentInfo`,
`ClosingPrice/GetClosingPriceInfo`, `Fund/GetETFByInsCode`) are verified
against the real `tsetmc` package's typed source. The bulk
"all-commodity-funds-in-one-call" endpoint exists
(`fetch_bulk_commodity_funds`) but isn't wired into `service.py` by
default — the per-instrument concurrent-fetch path is, because it's the
verified one.

**Exceptions**: `UnknownInstrumentError`, `UnknownGroupError`,
`TsetmcUnavailableError`, `AllInstrumentsUnavailableError`.

**Second, separate concern in the same domain — gold retail-price platforms**:
`GET /api/v1/commodities/gold/platforms` aggregates six external
platforms (hamrahgold, digikala, technogold, talasea, milligold,
melligold), fetched concurrently every 60s (`settings.gold_platform_poll_interval`,
matching "every 1 min") by `GoldPlatformPollingTask`, cached via
`RedisCache` (or `TTLCache` if `LOTUS_REDIS_URL` unset — same dev/prod
split as the OTP store) with the same cache-hit-or-live-fetch pattern
`crypto` uses. Deliberately a *separate* client ABC
(`clients/platform_base.py::GoldPricePlatformClient`) and separate
service (`platform_service.py`) from the TSETMC code above — different
data shape (one current price per platform, not per-instrument), so it
copies `crypto`'s `ExchangeClient` shape instead of `CommodityDataClient`'s.

**This is the single largest unverified surface in the project**: all
six platforms block automated fetching and have no public API docs, so
none of their response shapes could be confirmed — see §3 and
`clients/platform_base.py`'s module docstring for the full explanation,
including the diagnostic-error mechanism (`find_number`/`require_parsed`
in that file) that surfaces the real response's keys when a field-name
guess is wrong, so fixing it doesn't require re-reverse-engineering from
scratch.

**Exceptions (platform-specific)**: `GoldPlatformUnavailableError`,
`AllGoldPlatformsUnavailableError`.

---

## 7. What's been tested, and how

**Every domain has been booted and exercised end-to-end** via
`fastapi.testclient.TestClient` in a disposable copy of the project,
with:
- `legacy/lotus_monitor.py` and `legacy/strategy_engine.py` replaced
  with minimal hand-written stubs (real files were never available —
  see §3). This means `market`/`portfolio` business logic is verified at
  the *plumbing* level (routes → services → DB → response shape) but
  **not** against real IME data or real strategy math.
- External HTTP mocked at the `httpx.AsyncClient.get` level for `crypto`
  (fake exchange responses), `commodities`' TSETMC calls (fake TSETMC
  responses), and `commodities`' gold-platform calls (fake responses for
  all six platforms, including one deliberately malformed response to
  confirm the diagnostic-error path fires correctly) — no live network
  path to `binance.com`/`tsetmc.com`/`hamrahgold.com`/etc. existed in the
  build environment.
- `users`' and the gold-platform cache's Redis dependency tested against
  `fakeredis` (in-memory, same wire protocol) — never against a real
  Redis server.

**Specific scenarios verified, not just "endpoint returns 200"**:
partial exchange/instrument/platform failure (one source down, others
still return data), refresh-token rotation + cascade revocation on
reuse, OTP attempt-cap lockout (including the correct code being
rejected once attempts are exhausted), username collision between two
different users (and non-collision when a user re-submits their own
username), admin self-demotion guard, cache hit/miss behavior (including
confirming a cache hit makes zero new HTTP calls), the
strategy→position atomic-conversion transaction, and the gold-platform
diagnostic-error message correctly surfacing a malformed response's real
top-level keys.

**Never tested**: anything against real external services (IME, TSETMC,
the six gold retail platforms, Binance, Coinbase, Kraken, Redis, an SMS
provider). **Before trusting this in production, re-run equivalent
smoke tests against the real services** — mocked-HTTP tests prove the
code's logic is internally consistent, not that it's compatible with
what those services actually return today. This matters most for the
six gold retail-price platforms specifically (§3, §6.5) — those mocked
tests used *invented* response shapes, not shapes based on any real
observation, unlike every other domain's mocks.

---

## 8. Conventions to keep following

If you're continuing this project (as an AI picking this up, or a human
developer), these are load-bearing conventions — breaking them
inconsistently will make the codebase worse, not just different:

1. **New domain → new folder under `app/domains/`.** Never add a new
   top-level `app/<something>/` folder for a feature. Copy `crypto`'s or
   `commodities`' file layout as a starting template — they're the most
   recently built and most consistently follow every convention below.
2. **New domain's routes get a version prefix**: `/api/v1/<domain>/...`.
   `market`/`portfolio` are unprefixed (`/api/...`) because that's what
   existed before versioning was adopted — don't add a prefix to them
   retroactively (breaks existing clients for no gain), and don't leave
   a new domain unprefixed either (see `ARCHITECTURE.md`'s versioning
   section for the reasoning).
3. **New domain's DB tables go on `SharedBase`**, not a new database
   file, unless there's a specific reason to isolate it (there hasn't
   been one since `market`/`portfolio`).
4. **Multiple external data sources for one concept → one ABC in
   `clients/`, one implementation per source.** This is the
   `ExchangeClient`/`CommodityDataClient` pattern. Don't hardcode a
   single provider if there's any chance of a second one.
5. **Partial failure across multiple sources → gather-then-triage, not
   gather-then-raise-on-first-failure.** Copy `crypto.service.fetch_all`'s
   shape: successes and errors both collected, only raise if *everything*
   failed.
6. **Every domain's exceptions subclass `LotusError`**
   (`app/core/exceptions.py`) so the one global handler covers them — no
   per-domain exception-handler registration needed.
7. **Repository methods open their own short-lived `AsyncSession` and
   commit immediately** — never thread a request-scoped session through
   a repository that's also called from a background task.
8. **Background loops are `asyncio.Task`s started/stopped in `main.py`'s
   `lifespan`**, matching `FetchTask`/`CryptoPollingTask`/
   `CommodityPollingTask`'s shape — not a second scheduling mechanism.
9. **Settings**: one field per concern in `app/core/config.py`, grouped
   under a `# -- <Domain> --` comment, mirrored into `.env.example`
   immediately. Don't invent a second config mechanism for a new domain.
10. **`LotusError` should eventually be renamed `AppError`** — flagged
    back when there were 3 domains, still true now with 5. Low-risk,
    purely mechanical rename; just hasn't been prioritized.

---

## 9. Suggested next steps, roughly in priority order

1. **Verify the six gold retail-price platform response shapes against
   real requests** (§3, §6.5) before deploying
   `GET /api/v1/commodities/gold/platforms` — this is now the single
   largest unverified surface in the project, larger than the
   `lotus_monitor.py`/`strategy_engine.py` gap below, because it's *six*
   independent unknowns rather than two, and none had any typed
   reference to check against. Fastest path: `curl`/browser devtools
   against each URL (listed in §6.5, exact URLs in each `clients/*.py`
   file), then update that file's `_parse`/field-name guesses to match.
2. Get the real `legacy/lotus_monitor.py` and `legacy/strategy_engine.py`
   from the project owner and re-run the smoke tests against them
   instead of stubs (see §7).
3. Decide on the Binance USDT/USD symbol-mapping question (§6.3) before
   relying on that exchange for that symbol.
4. Verify `fetch_bulk_commodity_funds`'s field names against a real
   TSETMC response (§6.5) if the per-instrument concurrent-fetch
   approach turns out to be too slow or too heavy on TSETMC at scale.
5. Replace `LogOtpSender` with a real SMS provider and set
   `LOTUS_REDIS_URL` before any real user-facing deployment (§3) —
   note `LOTUS_REDIS_URL` now also gates the gold-platform cache, not
   just OTP storage, so this is higher-value than before.
6. Confirm the duplicate-ISIN row in `commodities/registry.py` with
   whoever supplied that list (§3).
7. When ready to add a 6th domain (or silver, or a second crypto/gold
   data source): re-read `ARCHITECTURE.md` in full first, then follow
   §8 above.
