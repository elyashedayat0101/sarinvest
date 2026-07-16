"""
app/domains/portfolio/deps.py
================================
Split out of the old `app/api/deps.py` — the portfolio-domain half.
`get_market_repo`/`valid_fund_id` (used by the strategy endpoints below,
which need live market prices) come from `app.domains.market.deps`
instead — an intentional cross-domain dependency, not an oversight; see
`router.py`'s module docstring.
"""
from __future__ import annotations

from fastapi import Request

from app.domains.portfolio.repository import PortfolioRepository


def get_portfolio_repo(request: Request) -> PortfolioRepository:
    return request.app.state.portfolio_repo
