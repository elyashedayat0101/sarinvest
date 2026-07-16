"""
app/schemas/market.py
=======================
The Flask app built every JSON response by hand with jsonify({...}).
That means field names, types, and nullability were only ever documented
by reading the route body. These Pydantic v2 models are that documentation
made executable: FastAPI validates outgoing data against them AND uses
them to generate the OpenAPI schema (Swagger UI / ReDoc) for free.

Field types below are inferred from how each value is used in
lotus_server.py (e.g. `c.volume:,` formatting implies int, `c.strike`
compared with float arithmetic implies float). If the real `Contract`
class in lotus_monitor.py disagrees, tighten these accordingly — see
README_MIGRATION.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    fund: str
    desc: str
    opt: str  # "C" | "P"
    strike: float
    last: float
    settle: float
    prev_settle: float
    chg_pct: float
    volume: int
    value: float
    high: float
    low: float
    oi: int
    d_oi: int
    demand: int
    supply: int
    buy_orders: int
    sell_orders: int
    expiry_j: str
    expiry_g: Optional[str] = None
    month_fa: str
    days_to_expiry: Optional[int] = None
    iv: Optional[float] = None
    delta: Optional[float] = None
    theta: Optional[float] = None
    spot_estimate: Optional[float] = None


class AvailableContractOut(BaseModel):
    code: str
    fund: str
    fund_name_fa: str
    opt: str
    strike: float
    expiry_j: str
    expiry_g: str
    month: str
    last: float
    settle: float
    iv: Optional[float] = None
    delta: Optional[float] = None
    spot: Optional[float] = None
    days: Optional[int] = None


class AlertOut(BaseModel):
    tag: str
    level: str  # "info" | "warn" | "critical"
    msg: str
    code: str


class MostActiveOut(BaseModel):
    code: str
    opt: str
    strike: float
    volume: int


class AnalysisOut(BaseModel):
    call_vol: int
    put_vol: int
    call_oi: int
    put_oi: int
    pcr_vol: Optional[float] = None
    pcr_oi: Optional[float] = None
    avg_call_iv: Optional[float] = None
    avg_put_iv: Optional[float] = None
    most_active: List[MostActiveOut] = []


class InsightOut(BaseModel):
    kind: str  # "skew" | "idea" | "warn"
    text: str


class LotusResponse(BaseModel):
    fund: str
    fund_name_fa: str
    fund_name_en: str
    contracts: List[ContractOut]
    spot: Dict[str, float]
    max_pain: Dict[str, float]
    alerts: List[AlertOut]
    analysis: AnalysisOut | dict
    insights: List[InsightOut]
    fetched_at: Optional[str] = None
    server_now: str
    cycle: int
    live: bool
    last_error: Optional[str] = None
    poll_interval: float
    last_duration: float


class FundSummaryOut(BaseModel):
    id: str
    name_fa: str
    name_en: str
    live: bool
    contract_count: int
    last_error: Optional[str] = None
    fetched_at: Optional[str] = None


class FundHealthOut(BaseModel):
    live: bool
    contract_count: int
    last_error: Optional[str] = None
    cycle: int


class HealthResponse(BaseModel):
    live: bool
    cycle: int
    last_error: Optional[str] = None
    last_duration: float
    poll_interval: float
    total_fetches: int
    total_skips: int
    persist_queue: int
    server_now: str
    funds: Dict[str, FundHealthOut]


class ContractDetailOut(BaseModel):
    code: str
    date: str
    summary: Optional[dict] = None
    trades: list = []
    intraday: list = []
    ohlc: list = []
