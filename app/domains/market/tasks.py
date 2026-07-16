"""
app/domains/market/tasks.py
==============================
Combines what were `services/fetch_service.py` (`FetchService` ->
renamed `FetchTask` here) and `services/persist_service.py`
(`PersistService` -> `PersistTask`) into one file, matching the
`tasks.py` convention established by `domains/crypto/tasks.py` — one
file per domain for "background loops started/stopped in lifespan",
rather than a `services/` layer name that would collide with the
business-logic-orchestration meaning `service.py` has in the crypto
domain (market's domain doesn't need a separate orchestration layer;
`repository.py` + these two tasks + `router.py` covers it).

FetchTask
---------
Replaces `FetchThread(threading.Thread)` from the original Flask app.
Runs as an `asyncio.Task`; the one genuinely blocking call per cycle —
`self._fetcher.fetch()`, an 8-15s network round trip per the original
comments — runs via `asyncio.to_thread(...)` so the event loop stays
free for every other request in the meantime.

PersistTask
-----------
Replaces the old `_persist_worker()` thread. Persistence goes through
SQLAlchemy's async engine now, so these calls are natively non-blocking —
no thread-pool wrapping needed for the DB writes themselves. The queue
between the two tasks is still a plain `queue.Queue` (thread-safe stdlib),
not `asyncio.Queue`, because `FetchTask`'s producer side still runs on a
worker thread via `asyncio.to_thread` — `asyncio.Queue` is only safe when
every caller is on the event-loop thread.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import time
import traceback
from datetime import datetime
from typing import Callable

from app.core.config import Settings
from app.domain.alerts import compute_alerts_persian
from app.domain.options_math import compute_delta, compute_max_pain, compute_theta, group_by_expiry
from app.domains.market.repository import MarketRepository
from app.domains.market.state import AppState
from app.domains.portfolio.repository import PortfolioRepository

log = logging.getLogger("lotus.fetch")
persist_log = logging.getLogger("lotus.persist")

_SENTINEL = object()


class FetchTask:
    def __init__(
        self,
        state: AppState,
        settings: Settings,
        fund_configs: dict,
        persist_queue: "queue.Queue",
        fetcher_factory: Callable,
        history_factory: Callable,
    ):
        self.state = state
        self.settings = settings
        self.fund_configs = fund_configs
        self.persist_queue = persist_queue
        self._fetcher = fetcher_factory(log)
        self._histories = {fid: history_factory() for fid in fund_configs}
        self.interval = settings.poll_interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="fetch-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def wait_for_first_fetch(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state.live:
                return True
            await asyncio.sleep(1)
        return False

    async def _run(self) -> None:
        log.info("fetch loop started — interval=%ss", self.interval)
        try:
            while True:
                t_start = time.monotonic()
                try:
                    # The blocking network+CPU work happens off the event loop.
                    await asyncio.to_thread(self._one_cycle_sync)
                except Exception as e:
                    log.error("fetch cycle error: %s\n%s", e, traceback.format_exc())
                    self.state.update_global(live=False, last_error=f"Cycle error: {e}")

                elapsed = time.monotonic() - t_start
                sleep_for = max(1.0, self.interval - elapsed)
                self.state.update_global(last_duration=elapsed)

                if elapsed > self.interval * 0.9:
                    new_iv = min(self.settings.max_poll_interval, round(elapsed * 1.5))
                    if new_iv > self.interval:
                        log.info("auto-adjusting interval %s -> %s", self.interval, new_iv)
                        self.interval = new_iv
                        self.state.update_global(poll_interval=new_iv)

                log.info("cycle took %.1fs — sleeping %.1fs", elapsed, sleep_for)
                await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            log.info("fetch loop cancelled — shutting down")
            raise

    # -- runs inside a worker thread via asyncio.to_thread --
    def _one_cycle_sync(self) -> None:
        raw_by_fund = self._fetcher.fetch()  # {fund_id: [raw_row, ...]}
        total_rows = sum(len(rows) for rows in raw_by_fund.values())
        log.info("fetch: %s rows across %s funds", total_rows, len(raw_by_fund))

        if not any(raw_by_fund.values()):
            self.state.update_global(
                live=False,
                last_error="فید خالی — سرور IME پاسخ نداد",
                total_skips=self.state.total_skips + 1,
            )
            return

        fetch_ts = datetime.now()
        self.state.update_global(cycle=self.state.cycle + 1)
        cycle = self.state.cycle
        any_live = False

        for fund_id, rows in raw_by_fund.items():
            fund_cfg = self.fund_configs[fund_id]
            if not rows:
                self.state.update_fund(fund_id, live=False, last_error="بدون داده در این چرخه")
                continue

            # NOTE: Contract/estimate_spot/implied_vol come from the existing
            # lotus_monitor module — imported lazily inside the task that
            # owns the fetch loop so the rest of the app has no hard
            # dependency on it (see legacy/README.md).
            from legacy.lotus_monitor import Contract, estimate_spot, implied_vol

            contracts = [Contract(r, fund_cfg) for r in rows]
            history = self._histories[fund_id]
            history.update(contracts)

            spot_map = estimate_spot(contracts)
            iv_map, greeks_map = {}, {}
            for c in contracts:
                S = spot_map.get(c.expiry_j)
                T = c.years_to_expiry
                p = c.ref_price
                if S and T and T > 0 and p > 0:
                    try:
                        iv = implied_vol(p, S, c.strike, T, c.opt == "C")
                    except Exception:
                        iv = None
                    if iv:
                        iv_map[c.code] = iv
                        greeks_map[c.code] = {
                            "delta": round(compute_delta(S, c.strike, T, iv, c.opt == "C"), 4),
                            "theta": round(compute_theta(S, c.strike, T, iv, c.opt == "C"), 2),
                        }

            by_expiry = group_by_expiry(contracts)
            max_pain = compute_max_pain(by_expiry)
            alerts = compute_alerts_persian(contracts, history)

            self.state.update_fund(
                fund_id,
                contracts=contracts, alerts=alerts, spot_map=spot_map,
                iv_map=iv_map, greeks_map=greeks_map, max_pain=max_pain,
                fetch_ts=fetch_ts, live=True, last_error=None, cycle=cycle,
            )
            any_live = True

            try:
                self.persist_queue.put_nowait(
                    (fund_id, contracts, fetch_ts, cycle, spot_map, iv_map, greeks_map, alerts)
                )
            except queue.Full:
                log.warning("persist queue full — dropping %s snapshot", fund_id)

        self.state.update_global(
            live=any_live,
            last_error=None if any_live else "هیچ صندوقی داده نداشت",
            total_fetches=self.state.total_fetches + 1,
        )


class PersistTask:
    def __init__(self, persist_queue: "queue.Queue", market_repo: MarketRepository, portfolio_repo: PortfolioRepository):
        self.queue = persist_queue
        self.market_repo = market_repo
        self.portfolio_repo = portfolio_repo
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="persist-loop")

    async def stop(self) -> None:
        self.queue.put(_SENTINEL)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _run(self) -> None:
        persist_log.info("persist loop started")
        while True:
            item = await asyncio.to_thread(self._get_next)
            if item is _SENTINEL:
                persist_log.info("persist loop received shutdown sentinel")
                return
            if item is None:
                continue

            fund_id, contracts, fetch_ts, cycle, spot_map, iv_map, greeks_map, alerts = item
            try:
                await self.market_repo.upsert_snapshot(contracts, fetch_ts, cycle, spot_map, iv_map, greeks_map)
                await self.market_repo.log_alerts(alerts, fetch_ts)
                await self.portfolio_repo.update_market_cache(contracts, spot_map, iv_map, greeks_map)
            except Exception as e:
                persist_log.error("persist error (%s): %s\n%s", fund_id, e, traceback.format_exc())

    def _get_next(self):
        try:
            return self.queue.get(timeout=5)
        except queue.Empty:
            return None
