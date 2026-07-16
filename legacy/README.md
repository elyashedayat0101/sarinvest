# legacy/

**Included and real:**

- `fund_config.py` ✅ — your actual file, unchanged.

**No longer needed here — replaced, not just wrapped:**

`lotus_db.py` and `portfolio_db.py` are **gone from this folder on purpose**.
Their raw-`sqlite3` schema and query logic have been fully reimplemented
with SQLAlchemy 2.0 async:

- Schema → `app/db/models/market.py` (was `lotus_db.py`'s `SCHEMA`) and
  `app/db/models/portfolio.py` (was `portfolio_db.py`'s `PORTFOLIO_SCHEMA`
  + `STRATEGY_SCHEMA_ADDITION`) — every table, column, `CHECK` constraint,
  foreign key, and index ported 1:1 (verified by inspecting the actual
  `CREATE TABLE` SQL SQLAlchemy generates — see README_MIGRATION.md).
- Query/write logic → `app/repositories/market_repo.py` (was `LotusDB`)
  and `app/repositories/portfolio_repo.py` (was `PortfolioDB`) — same
  method names, same behavior (one deliberate exception, documented in
  `portfolio_repo.py`'s module docstring: `convert_strategy_to_positions`
  is now atomic, where the original had a transaction-boundary quirk).
- Jalali calendar math → `app/domain/jalali.py` (was the free functions at
  the top of `portfolio_db.py`) — pure functions, unchanged logic, moved
  because they have nothing to do with persistence.

You do not need to provide these two files anymore. If you have an
existing `lotus_options.db` / `lotus_portfolio.db` from the old app, they
should open fine as-is — SQLAlchemy's `create_all` only creates tables
that don't already exist, and the schema is byte-for-byte compatible.

**Still needed from you** — drop these in unchanged:

- `lotus_monitor.py` — must export `Fetcher`, `Contract`, `Config`,
  `History`, `estimate_spot`, `implied_vol`, `norm_cdf`, `make_logger`
- `strategy_engine.py` — must export `evaluate_strategy`, `net_cost`,
  `compute_payoff_curve`, `compute_bounds_and_breakevens`,
  `probability_of_profit`, `suggest_strategies`, and the `template_*`
  functions

Both are still stubbed during development and smoke-tested against those
stubs, not real code — see README_MIGRATION.md.
