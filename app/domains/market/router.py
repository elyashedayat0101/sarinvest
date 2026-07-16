"""
app/domains/market/router.py
===============================
Combines what were `app/api/v1/funds.py`, `health.py`, and `market.py`
into one file — all three are "read the live market state" endpoints for
this one domain, and combined they're under 200 lines, well short of
where splitting back out would earn its keep. If this grows
significantly (e.g. a dozen more market endpoints), split by resource
again then, following the pattern `domains/portfolio/router.py` already
uses for its three internal sub-routers.

Mounted with `prefix=""` at `/api/...` — i.e. still unversioned, exactly
like before this domain migration. See ARCHITECTURE.md's versioning
section for why these URLs are frozen as-is while every *new* domain
(crypto) mounts under `/api/v1/...` instead.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request

from app.core.exceptions import FundNotFoundError
from app.domain.alerts import alerts_to_api
from app.domains.market.deps import get_fund_configs, get_market_repo, valid_fund_id
from app.domains.market.repository import MarketRepository
from app.domains.market.schemas import (
    AlertOut, AvailableContractOut, ContractDetailOut, FundSummaryOut, HealthResponse, LotusResponse,
)
from app.domains.market.serializers import build_analysis, build_insights, contract_to_api

router = APIRouter(tags=["market"])


# ---- health ----

@router.get("/health", response_model=HealthResponse)
async def health(request: Request, market_repo: MarketRepository = Depends(get_market_repo)):
    snap = market_repo.health()
    persist_queue = request.app.state.persist_queue
    return HealthResponse(
        **{k: v for k, v in snap.items() if k != "funds"},
        persist_queue=persist_queue.qsize(),
        server_now=datetime.now().isoformat(),
        funds=snap["funds"],
    )


# ---- funds ----

@router.get("/funds", response_model=List[FundSummaryOut])
async def list_funds(
    fund_configs: dict = Depends(get_fund_configs),
    market_repo: MarketRepository = Depends(get_market_repo),
):
    out = []
    for fid, cfg in fund_configs.items():
        fs = market_repo.snapshot_fund(fid)
        out.append(FundSummaryOut(
            id=fid,
            name_fa=cfg.name_fa,
            name_en=cfg.name_en,
            live=fs.live,
            contract_count=len(fs.contracts),
            last_error=fs.last_error,
            fetched_at=fs.fetch_ts.isoformat() if fs.fetch_ts else None,
        ))
    return out


# ---- market data ----

@router.get("/lotus", response_model=LotusResponse)
async def get_lotus(
    request: Request,
    fund_id: str = Depends(valid_fund_id),
    fund_configs: dict = Depends(get_fund_configs),
    market_repo: MarketRepository = Depends(get_market_repo),
):
    """Equivalent of the old `GET /api/lotus?fund=...` (fund defaults to
    `default_fund` from settings, same as the old `DEFAULT_FUND` constant,
    so existing bookmarks/clients keep working unchanged)."""
    fs = market_repo.snapshot_fund(fund_id)
    app_state = request.app.state.app_state
    contracts = fs.contracts

    return LotusResponse(
        fund=fund_id,
        fund_name_fa=fund_configs[fund_id].name_fa,
        fund_name_en=fund_configs[fund_id].name_en,
        contracts=[contract_to_api(c, fs) for c in contracts],
        spot=fs.spot_map,
        max_pain=fs.max_pain,
        alerts=[AlertOut(**a) for a in alerts_to_api(fs.alerts)],
        analysis=build_analysis(contracts, fs) if contracts else {},
        insights=build_insights(contracts, fs) if contracts else [],
        fetched_at=fs.fetch_ts.isoformat() if fs.fetch_ts else None,
        server_now=datetime.now().isoformat(),
        cycle=fs.cycle,
        live=fs.live,
        last_error=fs.last_error,
        poll_interval=app_state.poll_interval,
        last_duration=round(app_state.last_duration, 2),
    )


@router.get("/contracts/available", response_model=List[AvailableContractOut])
async def available_contracts(
    request: Request,
    fund: Optional[str] = Query(default=None, description="Restrict to one fund; omit for all funds"),
    fund_configs: dict = Depends(get_fund_configs),
    market_repo: MarketRepository = Depends(get_market_repo),
):
    if fund and fund not in fund_configs:
        raise FundNotFoundError(f"صندوق نامعتبر: {fund}")
    fund_ids = [fund] if fund else list(fund_configs.keys())

    rows = []
    for fid in fund_ids:
        fs = market_repo.snapshot_fund(fid)
        for c in fs.contracts:
            rows.append(AvailableContractOut(
                code=c.code, fund=fid, fund_name_fa=fund_configs[fid].name_fa,
                opt=c.opt, strike=c.strike, expiry_j=c.expiry_j,
                expiry_g=str(c.expiry_g) if c.expiry_g else "",
                month=c.month, last=c.last, settle=c.settle,
                iv=fs.iv_map.get(c.code),
                delta=fs.greeks_map.get(c.code, {}).get("delta"),
                spot=fs.spot_map.get(c.expiry_j),
                days=max(0, (c.expiry_g - date.today()).days) if c.expiry_g else None,
            ))
    return rows


@router.get("/contract/{code}", response_model=ContractDetailOut)
async def contract_detail(
    code: str,
    date_str: str = Query(default_factory=lambda: date.today().strftime("%Y-%m-%d"), alias="date"),
    market_repo: MarketRepository = Depends(get_market_repo),
):
    return await market_repo.get_contract_detail(code, date_str)


@router.get("/contract/{code}/trades")
async def contract_trades(
    code: str,
    date_str: str = Query(default_factory=lambda: date.today().strftime("%Y-%m-%d"), alias="date"),
    limit: int = 200,
    market_repo: MarketRepository = Depends(get_market_repo),
):
    trades = await market_repo.get_trades(code, date_str, limit)
    return {"code": code, "date": date_str, "trades": trades}


@router.get("/contract/{code}/intraday")
async def contract_intraday(
    code: str,
    date_str: str = Query(default_factory=lambda: date.today().strftime("%Y-%m-%d"), alias="date"),
    market_repo: MarketRepository = Depends(get_market_repo),
):
    series = await market_repo.get_intraday(code, date_str)
    return {"code": code, "date": date_str, "series": series}


@router.get("/contract/{code}/ohlc")
async def contract_ohlc(
    code: str,
    date_from: str = Query(default="2024-01-01", alias="from"),
    date_to: str = Query(default_factory=lambda: date.today().strftime("%Y-%m-%d"), alias="to"),
    limit: int = 120,
    market_repo: MarketRepository = Depends(get_market_repo),
):
    ohlc = await market_repo.get_ohlc(code, date_from, date_to, limit)
    return {"code": code, "ohlc": ohlc}


@router.get("/alerts")
async def alerts_recent(
    limit: int = 50,
    market_repo: MarketRepository = Depends(get_market_repo),
):
    return {"alerts": await market_repo.get_alerts_recent(limit)}
