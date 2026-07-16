"""
app/services/state.py
=======================
Design note — why this still uses `threading.Lock`, not `asyncio.Lock`:

The real `Fetcher.fetch()` and `LotusDB`/`PortfolioDB` calls are synchronous
and genuinely blocking (network I/O, sqlite disk I/O — the original code's
own comments say fetch takes 8-15s). To keep the FastAPI event loop free,
the fetch loop runs on a dedicated background thread via
`asyncio.to_thread` (see fetch_service.py), exactly like the old
`FetchThread`. Because the *writer* lives on a plain OS thread,
`asyncio.Lock` is the wrong tool here — it is only safe to acquire from
the event-loop thread that created it. `threading.Lock` is the correct,
safe choice for a lock shared between a background OS thread and
(briefly) the event loop.

Async route handlers below acquire this lock only to take an immediate,
allocation-only shallow copy — sub-millisecond, no I/O happens while held.
That is the same "hold the lock just long enough to snapshot" discipline
the original Flask app used; it is a well-established, deliberate
exception to "never call blocking code in async def", not an oversight.
Anything that could actually take meaningful time (sqlite writes on the
portfolio endpoints) is pushed through `starlette.concurrency.run_in_threadpool`
instead — see repositories/portfolio_repo.py.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class FundState:
    contracts: List[Any] = field(default_factory=list)
    alerts: List[Any] = field(default_factory=list)
    spot_map: Dict[str, float] = field(default_factory=dict)
    iv_map: Dict[str, float] = field(default_factory=dict)
    greeks_map: Dict[str, dict] = field(default_factory=dict)
    max_pain: Dict[str, float] = field(default_factory=dict)
    fetch_ts: Optional[datetime] = None
    cycle: int = 0
    live: bool = False
    last_error: Optional[str] = None


class AppState:
    """
    One instance, created in main.py's lifespan and handed to routers via
    dependency injection (app/api/deps.py) — no module-level globals, so
    it can be swapped for a fake in tests.
    """

    def __init__(self, fund_ids: list[str], poll_interval: float):
        self._lock = threading.Lock()
        self.funds: Dict[str, FundState] = {fid: FundState() for fid in fund_ids}
        self.cycle: int = 0
        self.live: bool = False
        self.last_error: Optional[str] = None
        self.poll_interval: float = poll_interval
        self.last_duration: float = 0.0
        self.total_fetches: int = 0
        self.total_skips: int = 0

    # -- writer-side API (called only from the fetch background thread) --
    def update_fund(self, fund_id: str, **kwargs) -> None:
        with self._lock:
            fs = self.funds[fund_id]
            for k, v in kwargs.items():
                setattr(fs, k, v)

    def update_global(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    # -- reader-side API (called from async route handlers) --
    def snapshot_fund(self, fund_id: str) -> FundState:
        with self._lock:
            fs = self.funds[fund_id]
            return FundState(**vars(fs))

    def snapshot_health(self) -> dict:
        with self._lock:
            return {
                "live": self.live,
                "cycle": self.cycle,
                "last_error": self.last_error,
                "last_duration": self.last_duration,
                "poll_interval": self.poll_interval,
                "total_fetches": self.total_fetches,
                "total_skips": self.total_skips,
                "funds": {
                    fid: {
                        "live": fs.live,
                        "contract_count": len(fs.contracts),
                        "last_error": fs.last_error,
                        "cycle": fs.cycle,
                    }
                    for fid, fs in self.funds.items()
                },
            }

    def find_contract_by_code(self, code: str):
        if not code:
            return None
        with self._lock:
            for fs in self.funds.values():
                for c in fs.contracts:
                    if c.code == code:
                        return c
        return None
