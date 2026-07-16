"""
app/domains/market/deps.py
=============================
Split out of the old `app/api/deps.py` — everything here is market-domain
specific (fund config, `AppState`, `MarketRepository`). `get_portfolio_repo`
moved to `app/domains/portfolio/deps.py` instead. This is the "domain owns
its own DI" half of the pattern described in ARCHITECTURE.md; the other
half — truly cross-cutting providers used by more than one domain — would
go in a slim `app/api/deps.py`, but nothing qualifies as that yet (even
`get_default_fund`/`valid_fund_id` are market-specific, not app-wide).
"""
from __future__ import annotations

from fastapi import Depends, Request

from app.core.exceptions import FundNotFoundError
from app.domains.market.repository import MarketRepository
from app.domains.market.state import AppState


def get_app_state(request: Request) -> AppState:
    return request.app.state.app_state


def get_market_repo(request: Request) -> MarketRepository:
    return request.app.state.market_repo


def get_fund_configs(request: Request) -> dict:
    return request.app.state.fund_configs


def get_default_fund(request: Request) -> str:
    return request.app.state.settings.default_fund


def valid_fund_id(
    fund: str | None = None,
    fund_configs: dict = Depends(get_fund_configs),
    default_fund: str = Depends(get_default_fund),
) -> str:
    """
    Path/query param dependency replacing the repeated

        fund_id = request.args.get("fund", DEFAULT_FUND)
        if fund_id not in FUNDS:
            return jsonify({"error": ...}), 400

    block that appeared in five different Flask routes. Raising
    `FundNotFoundError` here means each route no longer needs the check.
    """
    fund_id = fund or default_fund
    if fund_id not in fund_configs:
        raise FundNotFoundError(f"صندوق نامعتبر: {fund_id}. گزینه‌ها: {list(fund_configs)}")
    return fund_id
