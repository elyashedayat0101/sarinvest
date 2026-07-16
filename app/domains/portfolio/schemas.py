"""
app/domains/portfolio/schemas.py
===================================
Combines what were `schemas/portfolio.py` and `schemas/strategy.py`.
Strategies aren't split into their own top-level domain here — they're a
sub-concern of portfolio management: a `Strategy`'s legs convert directly
into `Position` rows (`strategy_legs.linked_position_id -> positions.id`
is a real foreign key), they live in the same physical database
(`portfolio_db_url`), and the original `portfolio_db.py` already treated
them as one class's responsibility. Splitting further would mean either
duplicating the atomic-transaction logic in `convert_strategy_to_positions`
across two repository classes, or reintroducing cross-repository session
sharing — real complexity for a boundary that doesn't reflect how the
data actually relates. See ARCHITECTURE.md for the reasoning in full.
"""
from __future__ import annotations

from datetime import date as date_
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# ---- Portfolio / Position ----

class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class PortfolioOut(BaseModel):
    id: int
    name: str
    description: str = ""
    # extend with whatever PortfolioRepository.list_portfolios() actually returns


class PositionCreate(BaseModel):
    contract_code: str
    opt_type: Optional[Literal["C", "P"]] = None  # defaulted from live contract if omitted
    strike: Optional[int] = None
    expiry_jalali: Optional[str] = None
    expiry_gregorian: Optional[str] = None
    month_label: Optional[str] = None
    direction: Literal["long", "short"]
    quantity: int
    premium_paid: float
    open_date: Optional[date_] = None
    notes: str = ""


class PositionCloseRequest(BaseModel):
    close_price: float
    close_date: Optional[date_] = None


class NoteCreate(BaseModel):
    note: str = Field(min_length=1)


class IdOk(BaseModel):
    id: int
    ok: bool = True


class Ok(BaseModel):
    ok: bool = True


# ---- Strategy ----

class Leg(BaseModel):
    """
    Shape confirmed against the real `portfolio_db.save_strategy`, which
    does `leg['leg_type']` and `leg['action']` with no `.get()` fallback —
    both are hard-required or every strategy save raises a `KeyError`.
    `strategy_engine.py` itself is still not available; `extra="allow"`
    keeps this forward-compatible with whatever additional fields it
    expects, but do a final check once you can see it.

    The `opt_type`/`strike`/`contract_code`/`expiry_jalali` requirement
    for option legs isn't enforced by the DB layer itself —
    `convert_strategy_to_positions` passes whatever it's given straight
    into an INSERT, and all four are `NOT NULL` on `positions`. Found this
    by smoke-testing an option leg missing fields one at a time: each
    reached sqlite and raised a raw `IntegrityError` (500) instead of
    failing validation. Enforcing all four here turns that into one
    immediate, readable 422 instead of a different 500 per missing field.
    """
    model_config = {"extra": "allow"}

    leg_type: Literal["option", "stock"] = "option"
    contract_code: Optional[str] = None
    opt_type: Optional[Literal["C", "P"]] = None  # None for stock legs
    action: Literal["buy", "sell"]
    strike: Optional[int] = None
    expiry_jalali: Optional[str] = None
    expiry_gregorian: Optional[str] = None  # nullable in the DB, but needed for accurate days-remaining in P&L
    quantity: int
    entry_price: float
    entry_iv: Optional[float] = None

    @model_validator(mode="after")
    def _option_legs_need_option_fields(self) -> "Leg":
        if self.leg_type == "option":
            required = ("contract_code", "opt_type", "strike", "expiry_jalali")
            missing = [f for f in required if getattr(self, f) is None]
            if missing:
                raise ValueError(
                    f"option leg missing required field(s): {', '.join(missing)} "
                    "(all are NOT NULL on positions in portfolio_db)"
                )
        return self


class StrategyCalculateRequest(BaseModel):
    legs: List[Leg] = Field(min_length=1)
    spot: float = Field(gt=0)


class StrategySimulateRequest(BaseModel):
    legs: List[Leg] = Field(min_length=1)
    spot: float = Field(gt=0)
    spot_shock_pct: float = 0.0
    iv_shock_pct: float = 0.0
    days_forward: float = 0.0


class StrategySaveRequest(BaseModel):
    portfolio_id: int
    name: str
    strategy_type: str
    legs: List[Leg] = Field(min_length=1)
    spot: float = 0.0
    notes: str = ""


class StrategyCalcResponse(BaseModel):
    net_cost: float
    is_credit: bool
    entry_greeks: Dict[str, Any]
    per_leg: List[Dict[str, Any]]
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None
    max_profit_unbounded: bool
    max_loss_unbounded: bool
    breakevens: List[float]
    payoff_curve_today: list
    payoff_curve_expiry: list


class StrategySimulateResponse(BaseModel):
    shocked_spot: float
    days_forward: float
    iv_shock_pct: float
    total_pnl: float
    greeks: Dict[str, Any]
    per_leg: List[Dict[str, Any]]
    probability_of_profit: Optional[float] = None
    payoff_curve: list


class TemplateResponse(BaseModel):
    spot: float
    legs: List[Dict[str, Any]]
