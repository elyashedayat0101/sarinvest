"""
app/domains/portfolio/router.py
==================================
Three internal sub-routers (`_portfolio_router`, `_position_router`,
`_strategy_router`) combined into one exported `router` — kept as
separate `APIRouter` objects internally (clearer prefixes/tags per
resource) rather than three separate files, since the whole domain
here is ~320 lines combined, not unreasonable for one file. Split back
into `router/` a subpackage if this grows much further.

Note the position endpoints are mounted at the top level (`/position/...`,
not `/portfolio/.../position/...`) — that's not an accident, it matches
the original Flask app's URL scheme, which a naive port would have been
tempted to "fix" by nesting them under `/portfolio/`, breaking existing
clients.

The strategy endpoints (`suggest`, `template/...`) need live market
prices — an intentional cross-domain dependency on
`app.domains.market.deps`/`app.domains.market.repository`. Portfolio
management legitimately needs to read market data; it does not need to
*write* it, and doesn't here.

`strategy_engine.py` (evaluate_strategy, net_cost, compute_payoff_curve,
compute_bounds_and_breakevens, probability_of_profit, suggest_strategies,
template_*) still isn't part of the source we were given — imported
unchanged from `legacy.strategy_engine` with the exact argument shapes
the original Flask routes used.
"""
from __future__ import annotations

import statistics
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.exceptions import (
    InvalidStrategyTypeError, MarketDataUnavailableError, PositionNotFoundError, StrategyNotFoundError,
)
from app.domain.options_math import nearest_expiry
from app.domains.market.deps import get_market_repo, valid_fund_id
from app.domains.market.repository import MarketRepository
from app.domains.portfolio.deps import get_portfolio_repo
from app.domains.portfolio.repository import PortfolioRepository
from app.domains.portfolio.schemas import (
    IdOk, NoteCreate, Ok, PortfolioCreate, PositionCloseRequest, PositionCreate,
    StrategyCalculateRequest, StrategySaveRequest, StrategySimulateRequest, TemplateResponse,
)

router = APIRouter(tags=["portfolio"])


# =====================================================================
# Portfolio + Position
# =====================================================================
_portfolio_router = APIRouter(prefix="/portfolio", tags=["portfolio"])
_position_router = APIRouter(prefix="/position", tags=["position"])


@_portfolio_router.get("")
async def list_portfolios(repo: PortfolioRepository = Depends(get_portfolio_repo)):
    return await repo.list_portfolios()


@_portfolio_router.post("", response_model=IdOk)
async def create_portfolio(body: PortfolioCreate, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    pid = await repo.create_portfolio(body.name, body.description)
    return IdOk(id=pid)


@_portfolio_router.delete("/{pid}", response_model=Ok)
async def delete_portfolio(pid: int, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    await repo.delete_portfolio(pid)
    return Ok()


@_portfolio_router.get("/{pid}/summary")
async def portfolio_summary(pid: int, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    # NOTE: the old Flask handler caught every exception here and returned
    # a fabricated 200-OK payload with zeroed-out fields plus an "error"
    # key buried inside — a caller checking `response.ok` would never
    # notice a failed lookup. Letting it propagate to the global handler
    # (core/exceptions.py) now returns an honest 500 with a clear error.
    return await repo.get_summary(pid)


@_portfolio_router.post("/{pid}/position", response_model=IdOk)
async def add_position(
    pid: int,
    body: PositionCreate,
    portfolio_repo: PortfolioRepository = Depends(get_portfolio_repo),
    market_repo: MarketRepository = Depends(get_market_repo),
):
    c = market_repo.find_contract_by_code(body.contract_code)
    pos_id = await portfolio_repo.add_position(
        pid,
        contract_code=body.contract_code,
        opt_type=body.opt_type or (c.opt if c else "C"),
        strike=body.strike or (c.strike if c else 0),
        expiry_jalali=body.expiry_jalali or (c.expiry_j if c else ""),
        expiry_gregorian=body.expiry_gregorian or (str(c.expiry_g) if c and c.expiry_g else ""),
        month_label=body.month_label or (c.month if c else ""),
        direction=body.direction,
        quantity=body.quantity,
        premium_paid=body.premium_paid,
        # PortfolioRepository.add_position's `open_date_gregorian` param
        # is typed `str`. Passing a raw `datetime.date` "works" only
        # because sqlite3 has an implicit adapter for it — confirmed
        # deprecated as of Python 3.12. Format explicitly instead.
        open_date_gregorian=body.open_date.strftime("%Y-%m-%d") if body.open_date else None,
        notes=body.notes,
    )
    return IdOk(id=pos_id)


@_portfolio_router.get("/{pid}/strategies")
async def list_strategies(pid: int, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    return await repo.list_strategies(pid)


@_position_router.get("/{pos_id}")
async def get_position(pos_id: int, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    pos = await repo.get_position(pos_id)
    if not pos:
        raise PositionNotFoundError("موقعیت یافت نشد")
    return {
        "position": pos,
        "pnl": await repo.compute_position_pnl(pos),
        "notes": await repo.get_notes(pos_id),
    }


@_position_router.post("/{pos_id}/close", response_model=Ok)
async def close_position(
    pos_id: int, body: PositionCloseRequest, repo: PortfolioRepository = Depends(get_portfolio_repo)
):
    close_date_str = body.close_date.strftime("%Y-%m-%d") if body.close_date else None
    await repo.close_position(pos_id, body.close_price, close_date_str)
    return Ok()


@_position_router.delete("/{pos_id}", response_model=Ok)
async def delete_position(pos_id: int, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    await repo.delete_position(pos_id)
    return Ok()


@_position_router.post("/{pos_id}/note", response_model=Ok)
async def add_note(pos_id: int, body: NoteCreate, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    await repo.add_note(pos_id, body.note)
    return Ok()


# =====================================================================
# Strategy
# =====================================================================
_strategy_router = APIRouter(prefix="/strategy", tags=["strategy"])


@_strategy_router.get("/suggest")
async def suggest(
    fund_id: str = Depends(valid_fund_id),
    holds: str = Query(default="0"),
    market_repo: MarketRepository = Depends(get_market_repo),
):
    from legacy.strategy_engine import suggest_strategies

    fs = market_repo.snapshot_fund(fund_id)
    contracts = fs.contracts
    if not contracts:
        return {"suggestions": [], "error": "داده بازار در دسترس نیست"}

    calls = [c for c in contracts if c.opt == "C"]
    puts = [c for c in contracts if c.opt == "P"]
    cv, pv = sum(c.volume for c in calls), sum(c.volume for c in puts)
    pcr = (pv / cv) if cv else 1.0

    civ_vals = [fs.iv_map[c.code] for c in calls if c.code in fs.iv_map]
    piv_vals = [fs.iv_map[c.code] for c in puts if c.code in fs.iv_map]
    avg_civ = statistics.mean(civ_vals) if civ_vals else 0.30
    avg_piv = statistics.mean(piv_vals) if piv_vals else 0.30

    total_vol = sum(c.volume for c in contracts) or 1
    trend = sum(c.chg_pct * c.volume for c in contracts) / total_vol

    ctx = {
        "pcr_vol": pcr, "avg_call_iv": avg_civ, "avg_put_iv": avg_piv,
        "spot_trend_pct": trend, "holds_underlying": holds == "1",
    }
    return {"suggestions": suggest_strategies(ctx), "context": ctx}


@_strategy_router.post("/calculate")
async def calculate(body: StrategyCalculateRequest):
    from legacy.strategy_engine import (
        compute_bounds_and_breakevens, compute_payoff_curve, evaluate_strategy, net_cost,
    )

    legs = [leg.model_dump(exclude_none=True) for leg in body.legs]
    entry_eval = evaluate_strategy(legs, body.spot, days_forward=0)
    bounds = compute_bounds_and_breakevens(legs, body.spot)
    curve_today = compute_payoff_curve(legs, body.spot, days_forward=0)
    curve_expiry = compute_payoff_curve(legs, body.spot, days_forward=None)
    cost = net_cost(legs)

    return {
        "net_cost": cost,
        "is_credit": cost < 0,
        "entry_greeks": entry_eval["greeks"],
        "per_leg": entry_eval["per_leg"],
        "max_profit": bounds["max_profit"],
        "max_loss": bounds["max_loss"],
        "max_profit_unbounded": bounds["max_profit_unbounded"],
        "max_loss_unbounded": bounds["max_loss_unbounded"],
        "breakevens": bounds["breakevens"],
        "payoff_curve_today": curve_today,
        "payoff_curve_expiry": curve_expiry,
    }


@_strategy_router.post("/simulate")
async def simulate(body: StrategySimulateRequest):
    from legacy.strategy_engine import compute_payoff_curve, evaluate_strategy, probability_of_profit

    legs = [leg.model_dump(exclude_none=True) for leg in body.legs]
    spot_shock = body.spot_shock_pct / 100.0
    iv_shock = body.iv_shock_pct / 100.0
    shocked_spot = body.spot * (1 + spot_shock)

    result = evaluate_strategy(legs, shocked_spot, body.days_forward, iv_shock)

    ivs = [l.get("entry_iv") for l in legs if l.get("entry_iv")]
    avg_iv = statistics.mean(ivs) if ivs else 0.30
    pop = probability_of_profit(legs, shocked_spot, max(1, body.days_forward), iv_for_sim=avg_iv + iv_shock)
    curve = compute_payoff_curve(legs, shocked_spot, body.days_forward, iv_shock)

    return {
        "shocked_spot": round(shocked_spot, 0),
        "days_forward": body.days_forward,
        "iv_shock_pct": iv_shock * 100,
        "total_pnl": result["total_pnl"],
        "greeks": result["greeks"],
        "per_leg": result["per_leg"],
        "probability_of_profit": pop,
        "payoff_curve": curve,
    }


@_strategy_router.post("")
async def save_strategy(body: StrategySaveRequest, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    from legacy.strategy_engine import compute_bounds_and_breakevens, net_cost

    legs = [leg.model_dump(exclude_none=True) for leg in body.legs]
    bounds = compute_bounds_and_breakevens(legs, body.spot)
    analysis = {
        "net_cost": net_cost(legs),
        "max_profit": bounds["max_profit"],
        "max_loss": bounds["max_loss"],
        "breakevens": bounds["breakevens"],
    }
    sid = await repo.save_strategy(
        portfolio_id=body.portfolio_id, name=body.name, strategy_type=body.strategy_type,
        legs=legs, underlying_spot_at_entry=body.spot, analysis=analysis, notes=body.notes,
    )
    return {"id": sid, "ok": True}


@_strategy_router.get("/{sid}")
async def get_strategy(sid: int, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    s = await repo.get_strategy(sid)
    if not s:
        raise StrategyNotFoundError("استراتژی یافت نشد")
    return s


@_strategy_router.delete("/{sid}")
async def delete_strategy(sid: int, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    await repo.delete_strategy(sid)
    return {"ok": True}


@_strategy_router.post("/{sid}/convert")
async def convert_strategy(sid: int, repo: PortfolioRepository = Depends(get_portfolio_repo)):
    ids = await repo.convert_strategy_to_positions(sid)
    return {"ok": True, "position_ids": ids}


@_strategy_router.get("/template/{strategy_type}", response_model=TemplateResponse)
async def strategy_template(
    strategy_type: str,
    fund_id: str = Depends(valid_fund_id),
    units_held: float = 1000,
    unit_cost: Optional[float] = None,
    qty: int = 1,
    opt_type: str = "C",
    market_repo: MarketRepository = Depends(get_market_repo),
):
    from legacy.strategy_engine import (
        template_bear_put_spread, template_bull_call_spread, template_calendar_spread,
        template_covered_call, template_iron_condor, template_protective_put,
        template_short_strangle,
    )

    fs = market_repo.snapshot_fund(fund_id)
    contracts = fs.contracts
    if not contracts:
        raise MarketDataUnavailableError("داده بازار در دسترس نیست")

    near_exp = nearest_expiry(contracts)
    near_contracts = [c for c in contracts if c.expiry_j == near_exp]
    spot = fs.spot_map.get(near_exp) or next(iter(fs.spot_map.values()), 0)
    cost_basis = unit_cost if unit_cost is not None else spot

    builders = {
        "covered_call": lambda: template_covered_call(near_contracts, spot, units_held, cost_basis, qty),
        "protective_put": lambda: template_protective_put(near_contracts, spot, units_held, cost_basis, qty),
        "bull_call_spread": lambda: template_bull_call_spread(near_contracts, spot, qty),
        "bear_put_spread": lambda: template_bear_put_spread(near_contracts, spot, qty),
        "short_strangle": lambda: template_short_strangle(near_contracts, spot, qty),
        "iron_condor": lambda: template_iron_condor(near_contracts, spot, qty),
    }

    if strategy_type == "calendar_spread":
        expiries = sorted({c.expiry_j for c in contracts})
        if len(expiries) < 2:
            raise InvalidStrategyTypeError("برای کلندر اسپرد به دو سررسید متفاوت نیاز است")
        near_c = [c for c in contracts if c.expiry_j == expiries[0]]
        far_c = [c for c in contracts if c.expiry_j == expiries[1]]
        legs = template_calendar_spread(near_c, far_c, spot, opt_type, qty)
    elif strategy_type in builders:
        legs = builders[strategy_type]()
    else:
        raise InvalidStrategyTypeError("نوع استراتژی نامعتبر است")

    return TemplateResponse(spot=spot, legs=legs)


router.include_router(_portfolio_router)
router.include_router(_position_router)
router.include_router(_strategy_router)
