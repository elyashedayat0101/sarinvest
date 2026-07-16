# Migration Notes — Flask → FastAPI

> **Note**: this document is a historical record of the original
> Flask→FastAPI and raw-SQL→SQLAlchemy migrations. File paths mentioned
> below (`app/schemas/`, `app/repositories/`, `app/services/`, `app/api/`)
> reflect the codebase's structure *at that point* — a later
> restructuring moved everything into `app/domains/<domain>/` (package by
> feature). See **README.md** for the current file layout and
> **ARCHITECTURE.md** for why and how that later move happened. The
> *content* below (what changed from Flask, bugs found, SQLAlchemy
> rewrite rationale) is still accurate — only the file paths are dated.

## What was migrated vs. what was assumed

Only `lotus_server.py` was provided. It imports five other modules
(`strategy_engine`, `lotus_db`, `portfolio_db`, `lotus_monitor`,
`fund_config`) that were **not** included in the source. Those are kept
as-is and go in `legacy/` unchanged — see `legacy/README.md` for the
exact interface each one is assumed to expose. Every call site in the new
code uses the same function names, argument order, and call style
(`db.upsert_snapshot(contracts, fetch_ts, cycle, spot_map, iv_map,
greeks_map)`, etc.) as the original Flask routes, so if your real modules
match those signatures, nothing else needs to change.

This was smoke-tested end-to-end against hand-written stub versions of
all five modules (all 26 original routes + `/docs` + `/openapi.json`,
29 requests total) — every route returns correctly, including the error
paths (invalid fund → 400, invalid strategy type → 400, missing position
→ 404), and the background fetch/persist tasks start and shut down
cleanly under `TestClient`. If your real modules' signatures differ even
slightly, you'll get an immediate `TypeError` at the call site rather
than a silent failure, because everything now goes through explicit
function calls and typed Pydantic models instead of loose dict access.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # adjust as needed
cp /path/to/your/lotus_monitor.py legacy/
cp /path/to/your/strategy_engine.py legacy/
cp /path/to/your/index1.html .
cp -r /path/to/your/static .   # if you have one

uvicorn app.main:app --host 0.0.0.0 --port 5000
# or: python -m app.main
```

Interactive API docs are now free at `/docs` (Swagger) and `/redoc`.

## Route-for-route mapping

All original URLs are preserved exactly (`/api/lotus`, `/api/funds`,
`/api/health`, `/api/portfolio/...`, `/api/position/...`,
`/api/strategy/...`, `/api/contract/...`) — no `/v1` prefix was added to
the URLs even though the router *code* lives under `app/api/v1/` (that's
just internal organization so a real `/api/v2` can be added later without
touching v1). Existing frontend/API clients do not need to change any
URLs.

One correction was made while porting: `/api/position/<id>` and its
sub-routes were nested at the top level in the original Flask app (not
under `/api/portfolio/...`), and that's preserved here — a naive port
would have been tempted to nest them under the portfolio router since
they're portfolio-related, which would have silently broken existing
clients. They're a separate `position` router for that reason.

## Architectural changes and why

| Flask | FastAPI | Why |
|---|---|---|
| `threading.Thread` (`FetchThread`, persist worker) | `asyncio.Task`s started in `lifespan`, with the actual blocking I/O still run via `asyncio.to_thread` | Native cancellation on shutdown (`SIGTERM` included), no manual `threading.Event` polling loop |
| Module-level `db = LotusDB()`, `pdb = PortfolioDB()`, `_state` dict | Built once in `lifespan`, attached to `app.state`, handed out via `Depends(...)` | Testable — swap real DB/state for fakes with `app.dependency_overrides` instead of monkeypatching module globals |
| `threading.RLock` around `_state` | `threading.Lock` in `AppState`, still — **not** `asyncio.Lock` | The fetch loop's blocking work runs on a real OS thread via `to_thread`; `asyncio.Lock` is unsafe to acquire off the event-loop thread. `threading.Lock` held only for a sub-millisecond copy is the correct, deliberate choice here — see the long comment at the top of `app/services/state.py` |
| Persist queue: `queue.Queue` | Still `queue.Queue` (unchanged) | Same reasoning — producer and consumer are both worker threads, not event-loop code, so the thread-safe stdlib queue is correct; `asyncio.Queue` would not be |
| `jsonify({...})` built by hand per route | Pydantic v2 models (`app/schemas/`) + `response_model=` | Response shape is now enforced and documented automatically via OpenAPI |
| `data = request.get_json(); data["name"]` | Pydantic request models | Missing/malformed fields now return a structured `422` before your handler runs, instead of a raw `KeyError` → generic 500 |
| Errors: mix of `jsonify({"error":...}), 400` inline, and one route that **caught all exceptions and returned 200 anyway** (`/api/portfolio/<id>/summary`) | Typed exceptions (`app/core/exceptions.py`) + one global handler | The old summary endpoint's behavior — returning `200 OK` with a hidden `"error"` key on failure — was arguably a bug (a caller checking `response.ok` would never notice). It now propagates and returns a real error status. **If any client code relied on that silent-200 behavior, you'll need to update it.** |
| No CORS config | `CORSMiddleware`, configurable via `.env` | Explicit is safer than implicit; set `LOTUS_CORS_ORIGINS` for your real frontend origin(s) before deploying, don't ship the `["*"]` default to production |
| `app.run(threaded=True, use_reloader=False)` via Flask's dev server | `uvicorn` ASGI server | Flask's built-in server was never meant for production; `uvicorn`/`gunicorn -k uvicorn.workers.UvicornWorker` is the standard here — see Deployment below |

## Bugs found and fixed during the port

1. **`_current_spot_and_contracts`** computed a `near` variable via
   `sorted(..., key=lambda c: c.days_to_expiry_safe if hasattr(...) else 999)`
   and then never used it — dead code.
2. **`api_strategy_template`**'s nearest-expiry logic:
   `min({c.expiry_j for c in contracts}, key=lambda e: ... if hasattr(contracts[0], 'days_to_expiry') else 0)`.
   If `Contract` doesn't expose a `days_to_expiry` attribute (nothing else
   in the file suggests it does — `contract_to_api` computes days
   on-the-fly from `expiry_g` instead), the key function returns the
   constant `0` for every expiry, and `min()` over a **set** then returns
   whatever CPython's hash-order happens to put first — not necessarily
   the nearest expiry, and not guaranteed stable across runs/interpreter
   versions. Replaced with `app/domain/options_math.nearest_expiry()`,
   which always derives days-to-expiry the same way `contract_to_api`
   does, deterministically.
3. **Portfolio summary endpoint** silently converted every exception into
   a `200 OK` with zeroed fields — see table above.
4. **`alerts_to_api`**'s `code` extraction (`msg.split("—")[0]`) is
   fragile — kept unchanged (behavior-preserving port), but flagged here:
   if a Persian description ever contains an em dash before the contract
   code, this silently returns the wrong `code`. Consider having the
   alert-generation step carry `code` as a structured field instead of
   parsing it back out of the formatted message.

## SQLAlchemy rewrite (replacing raw sqlite3)

`lotus_db.py` and `portfolio_db.py` used hand-written `sqlite3` +
raw SQL strings. Both are now fully reimplemented with SQLAlchemy 2.0's
async ORM, and the schema now lives in `app/` instead of being external:

- **Models**: `app/db/models/market.py` and `app/db/models/portfolio.py`,
  ported column-for-column from the original `CREATE TABLE` strings —
  same table names, column names, types, `NOT NULL`/`CHECK`/`PRIMARY KEY`/
  `FOREIGN KEY` constraints, and indexes. Verified by actually creating
  both databases and dumping `sqlite_master` — the generated
  `CREATE TABLE` SQL matches the original schema constraint-for-constraint
  (see the table below for one example).
- **Engine/sessions**: `app/db/session.py` — one `AsyncEngine` per
  database (created once at startup, not per-call), with
  `PRAGMA journal_mode=WAL` / `PRAGMA foreign_keys=ON` applied via a
  `connect` event listener instead of being re-issued on every call.
- **Repositories**: `app/repositories/market_repo.py` and
  `portfolio_repo.py` were rewritten in place (method names unchanged, so
  **no router code changed**) to run SQLAlchemy `select`/`insert`/`update`/
  `delete` statements inside short-lived `AsyncSession`s instead of
  wrapping the old classes' methods in `run_in_threadpool`. This is a real
  behavior improvement, not just a style change: the queries are natively
  non-blocking now (via the `aiosqlite` driver) instead of calling
  synchronous `sqlite3` code from a thread pool.
- **Upserts**: `ON CONFLICT(...) DO UPDATE` (used for the contracts,
  daily_ohlc, and market_cache tables) is expressed via
  `sqlalchemy.dialects.sqlite.insert(...).on_conflict_do_update(...)`.
- **Jalali date math**: moved out of `portfolio_db.py` into
  `app/domain/jalali.py` — it's pure date-formatting logic with no
  database dependency, so it belongs with `options_math.py`/`alerts.py`,
  not `db/`.

### Confirmed schema fidelity (example)

Original (`positions` table, from `portfolio_db.py`):
```sql
CREATE TABLE positions (
    ...
    opt_type  TEXT NOT NULL CHECK(opt_type IN ('C','P')),
    direction TEXT NOT NULL CHECK(direction IN ('long','short')),
    status    TEXT DEFAULT 'open' CHECK(status IN ('open','closed','expired')),
    ...
);
```
Generated by the new `Position` model (dumped from a live database after
running the app):
```sql
CREATE TABLE positions (
    ...
    opt_type VARCHAR NOT NULL, direction VARCHAR NOT NULL, status VARCHAR NOT NULL,
    ...
    CONSTRAINT ck_positions_opt_type CHECK (opt_type IN ('C','P')),
    CONSTRAINT ck_positions_direction CHECK (direction IN ('long','short')),
    CONSTRAINT ck_positions_status CHECK (status IN ('open','closed','expired')),
    ...
);
```
(`TEXT` vs `VARCHAR` is cosmetic — SQLite treats them identically via type
affinity; the constraints and behavior are the same.)

### One deliberate behavior change

`PortfolioRepository.convert_strategy_to_positions` now runs as a single
atomic transaction. The original `portfolio_db.py` called
`self.add_position(...)` from inside `with self._conn():`, but
`add_position` opens its *own* separate connection — so each created
position actually committed independently of the "link leg to position"
and "mark strategy active" updates that followed. If a later leg had
failed, earlier legs' positions would already be permanently committed
while the strategy was never marked active — a partial-failure state the
original code could leave behind. The rewrite closes this gap: all
position inserts, leg links, and the status update happen inside one
`session.begin()` block, so it's all-or-nothing. Flagging this as a
behavior change in case anything downstream depended on the old
partial-commit semantics (nothing in the given source did).

### What this means for existing data

If you have existing `lotus_options.db` / `lotus_portfolio.db` files from
running the original Flask app, they should work as-is — the schema is
compatible (see above), and `app/db/session.py::create_all` only creates
tables that don't already exist (`CREATE TABLE IF NOT EXISTS` under the
hood), it won't touch or drop existing data.

### Dependencies added

`sqlalchemy[asyncio]>=2.0`, `aiosqlite>=0.20` (async SQLite driver),
`jdatetime>=5.0` (optional — `app/domain/jalali.py` already falls back to
a pure-Python algorithm if it's absent, same as the original
`portfolio_db.py` did).



The first draft of this migration guessed at the shape of these three
modules. You then provided the real source, and I smoke-tested the actual
app code against them (not stubs) — a few real mismatches turned up and
are now fixed:

1. **`Leg` schema was wrong.** The original draft had a `direction` field
   on `Leg`, guessed by analogy with `Position.direction`. The real
   `portfolio_db.save_strategy` does `leg['leg_type']` and `leg['action']`
   with no `.get()` fallback — both are hard-required, and `action`, not
   `direction`, is the real field name. Every `POST /api/strategy` would
   have raised a raw `KeyError` → 500. Fixed: `Leg` now has `leg_type`
   (default `"option"`) and `action: Literal["buy","sell"]`, matching the
   real `strategy_legs` table exactly.

2. **`portfolio_db.py` itself doesn't validate before inserting.**
   `convert_strategy_to_positions` reads a saved strategy's legs back out
   and passes them straight into `add_position` — but `positions.opt_type`,
   `.strike`, `.contract_code`, and `.expiry_jalali` are all `NOT NULL`.
   A strategy saved with an incomplete option leg (e.g. missing `opt_type`)
   would save fine, then blow up with a raw `sqlite3.IntegrityError` the
   moment someone tried to convert it to real positions. This is a
   pre-existing gap in `portfolio_db.py`, not something introduced by the
   migration — but since it's now visible, `Leg` has a `model_validator`
   that requires `contract_code`/`opt_type`/`strike`/`expiry_jalali` for
   any `leg_type="option"` leg, so this now fails as an immediate, readable
   422 at `POST /api/strategy` instead of a confusing 500 later at
   `POST /api/strategy/{id}/convert`. If `strategy_engine.py` legitimately
   produces option legs without one of these fields in some flow, this
   validator will need loosening — but as written it matches the DB's own
   constraints exactly, so loosening it just moves the failure back to sqlite.

3. **`open_date`/`close_date` were passed through as `datetime.date`
   objects.** `PortfolioDB.add_position`/`close_position` type their date
   parameters as `str`. Passing a `date` object "worked" only because
   sqlite3 has a legacy implicit adapter for it — confirmed via a direct
   test that this raises `DeprecationWarning: The default date adapter is
   deprecated as of Python 3.12`. Fixed: both routers now call
   `.strftime("%Y-%m-%d")` before handing the value to the repository.

4. **DB paths were hardcoded relative paths** (`Path("lotus_options.db")`,
   `Path("lotus_portfolio.db")`) inside the two DB classes. This was
   originally fixed with a `path=` override passed to `LotusDB`/`PortfolioDB`;
   that whole approach is now superseded by the SQLAlchemy rewrite (see
   the dedicated section above) — connection targets are now
   `LOTUS_LOTUS_DB_URL`/`LOTUS_PORTFOLIO_DB_URL` in `.env`, standard
   SQLAlchemy connection URLs rather than raw file paths.

5. **What I no longer need to guess about**: field names/types for
   `ContractOut`/`add_position`/`get_daily_ohlc`/etc. all matched the
   original draft's assumptions — no changes needed there.

6. **SQLite thread-safety concern from the first draft is resolved,
   differently than originally planned**: I'd flagged connection handling
   as a risk to check, then confirmed the original raw-`sqlite3` code
   already handled it correctly (fresh connection per call). The
   SQLAlchemy rewrite changes the *mechanism* again — one pooled
   `AsyncEngine` per database instead of a connection per call — but
   remains safe for the same reason: SQLAlchemy's async engine (via
   `aiosqlite`) manages connection handoff correctly across concurrent
   `asyncio` tasks, and `PRAGMA journal_mode=WAL` (still applied, now via
   a `connect` event listener) is what actually allows concurrent
   readers/writers without `database is locked` errors.


`lotus_monitor.py` and `strategy_engine.py` are still not available, so
the `Leg` model's `extra="allow"` and the assumed `Contract` attribute
names remain the last unverified surface — see `legacy/README.md`.


- **Field types in `app/schemas/market.py`** were inferred from usage
  (e.g. `c.volume:,` formatting implied `int`). Check them against the
  real `Contract` class and tighten/loosen as needed — a mismatch will
  currently surface as a `ResponseValidationError` (500) rather than
  silently wrong JSON, which is the intended tradeoff but worth knowing
  before you deploy.
- **`app/schemas/strategy.py::Leg`** uses `extra="allow"` because the
  real leg shape lives in `strategy_engine.py`, which wasn't provided.
  Once you can see it, replace `Leg` with an exact model — this is the
  biggest remaining "trust but verify" surface in the port.
- **CORS origins** — set `LOTUS_CORS_ORIGINS` explicitly; don't deploy
  with the `["*"]` default if the API will ever hold non-public data
  (portfolios/positions look like they might).
- **No authentication/authorization anywhere** — this was also true of
  the original Flask app (any client could create/delete portfolios and
  positions with no auth check). Not introduced or removed by this
  migration; flagging it because it's a pre-existing gap worth closing
  before this is exposed beyond localhost.
- **Static file serving**: `send_from_directory(".", "index1.html")` used
  the process's current working directory, which is fragile if you ever
  run the app from a different CWD (e.g. `systemd`, Docker `WORKDIR`
  mismatch). The FastAPI version serves `index1.html` from a path
  resolved at import time (`LOTUS_INDEX_FILE`, default `index1.html`,
  resolved relative to wherever you run the process from) and mounts
  `LOTUS_STATIC_DIR` — same relative-path caveat applies; set absolute
  paths via `.env` in production.

## Deployment

Flask's dev server (`app.run(...)`) was never production-grade. Recommended:

```bash
# single-process, good for this app's single-fetch-loop design:
uvicorn app.main:app --host 0.0.0.0 --port 5000 --workers 1

# do NOT use --workers > 1 without rethinking the background fetch loop —
# each worker process would run its own FetchService/PersistService,
# hammering the upstream feed N times and writing to SQLite from N
# processes concurrently. If you need multiple processes for HTTP
# throughput, move the fetch/persist loop into a separate process
# (e.g. a small standalone script or Celery/RQ worker) that writes to
# SQLite, and run N stateless API workers that only read.
```

A `Dockerfile` is not included since your existing deployment
tooling/base image wasn't provided — happy to add one if useful.

## Testing

`pyproject.toml` includes `pytest` + `pytest-asyncio` + `httpx` as dev
dependencies. `app.state.*` being set up entirely in `lifespan` means a
test can override `app.dependency_overrides[get_market_repo]` (etc.) with
fakes and never touch a real SQLite file or network fetch — see
`app/api/deps.py`'s docstring for the intended pattern. No test suite is
included since none existed in the original Flask app to port forward.
