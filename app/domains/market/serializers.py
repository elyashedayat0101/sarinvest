"""
app/domains/market/serializers.py
=============================
Ported from lotus_server.py's contract_to_api / build_analysis /
build_insights. Still hand-built dicts (not the Contract object directly)
because we don't control the Contract class's attribute names — the
resulting dicts are validated against app.domains.market.schemas.ContractOut etc.
by FastAPI's `response_model`, which is where type/shape mistakes now get
caught instead of surfacing as silently-wrong JSON to the frontend.
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Dict, List

from app.domains.market.state import FundState


def contract_to_api(c, fs: FundState) -> dict:
    days = None
    if c.expiry_g:
        days = max(0, (c.expiry_g - date.today()).days)
    return {
        "code": c.code,
        "fund": c.fund_id,
        "desc": c.desc,
        "opt": c.opt,
        "strike": c.strike,
        "last": c.last,
        "settle": c.settle,
        "prev_settle": c.prev_settle,
        "chg_pct": c.chg_pct,
        "volume": c.volume,
        "value": c.value,
        "high": c.high,
        "low": c.low,
        "oi": c.oi,
        "d_oi": c.d_oi,
        "demand": c.demand,
        "supply": c.supply,
        "buy_orders": c.buy_orders,
        "sell_orders": c.sell_orders,
        "expiry_j": c.expiry_j,
        "expiry_g": str(c.expiry_g) if c.expiry_g else None,
        "month_fa": c.month,
        "days_to_expiry": days,
        "iv": fs.iv_map.get(c.code),
        "delta": fs.greeks_map.get(c.code, {}).get("delta"),
        "theta": fs.greeks_map.get(c.code, {}).get("theta"),
        "spot_estimate": fs.spot_map.get(c.expiry_j),
    }


def build_analysis(contracts: list, fs: FundState) -> dict:
    calls = [c for c in contracts if c.opt == "C"]
    puts = [c for c in contracts if c.opt == "P"]
    cv, pv = sum(c.volume for c in calls), sum(c.volume for c in puts)
    co, po = sum(c.oi for c in calls), sum(c.oi for c in puts)
    ivm = fs.iv_map

    def avg_iv(lst):
        vals = [ivm[c.code] for c in lst if c.code in ivm]
        return statistics.mean(vals) if vals else None

    top5 = sorted(contracts, key=lambda c: c.volume, reverse=True)[:5]
    return {
        "call_vol": cv, "put_vol": pv, "call_oi": co, "put_oi": po,
        "pcr_vol": round(pv / cv, 3) if cv else None,
        "pcr_oi": round(po / co, 3) if co else None,
        "avg_call_iv": avg_iv(calls),
        "avg_put_iv": avg_iv(puts),
        "most_active": [
            {"code": c.code, "opt": c.opt, "strike": c.strike, "volume": c.volume}
            for c in top5
        ],
    }


def build_insights(contracts: list, fs: FundState) -> List[dict]:
    items = []
    ivm = fs.iv_map
    calls = [c for c in contracts if c.opt == "C"]
    puts = [c for c in contracts if c.opt == "P"]
    cv, pv = sum(c.volume for c in calls), sum(c.volume for c in puts)
    pcr = pv / cv if cv else None

    if pcr:
        if pcr > 1.3:
            items.append({"kind": "skew", "text": f"نسبت Put/Call = {pcr:.2f} — تقاضای پوشش ریسک بالاست."})
        elif pcr < 0.5:
            items.append({"kind": "skew", "text": f"نسبت Put/Call = {pcr:.2f} — جریان صعودی (کال‌محور)."})

    for code, iv in sorted(ivm.items(), key=lambda kv: kv[1])[:2]:
        c = next((x for x in contracts if x.code == code), None)
        if c and c.volume > 0:
            items.append({"kind": "idea", "text": f"{code}: IV پایین ({iv * 100:.0f}٪) — کاندیدای خرید."})

    thin = [c for c in contracts if 0 < c.volume < 10]
    if thin:
        items.append({"kind": "warn", "text": f"{len(thin)} قرارداد با نقدشوندگی بسیار پایین (<۱۰ لات)."})

    return items[:6]
