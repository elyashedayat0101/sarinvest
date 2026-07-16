"""
app/domain/options_math.py
============================
Pure, synchronous, CPU-bound math — ported verbatim in behavior from
lotus_server.py. Deliberately has ZERO Flask/FastAPI/db imports so it can
be unit-tested in isolation and safely called from a thread pool if it
ever becomes a bottleneck (it won't at this contract-count scale).
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def compute_delta(S: float, K: float, T: float, sigma: float, is_call: bool, r: float = 0.0) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 1.0 if (is_call and S > K) else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1) if is_call else norm_cdf(d1) - 1.0


def compute_theta(S: float, K: float, T: float, sigma: float, is_call: bool, r: float = 0.0) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    pdf = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
    if is_call:
        t = -S * pdf * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm_cdf(d2)
    else:
        t = -S * pdf * sigma / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * norm_cdf(-d2)
    return t / 365.0


def compute_max_pain(by_expiry: Dict[str, List]) -> Dict[str, float]:
    """`by_expiry` maps expiry_j -> list of Contract objects for that expiry."""
    result: Dict[str, float] = {}
    for expiry, contracts in by_expiry.items():
        strikes = sorted({c.strike for c in contracts if c.strike > 0})
        if not strikes:
            continue
        best = min(
            strikes,
            key=lambda K: sum(
                (max(0, K - c.strike) if c.opt == "C" else max(0, c.strike - K)) * c.oi
                for c in contracts if c.oi > 0
            ),
        )
        result[expiry] = best
    return result


def group_by_expiry(contracts: Iterable) -> Dict[str, list]:
    by_expiry: Dict[str, list] = {}
    for c in contracts:
        by_expiry.setdefault(c.expiry_j, []).append(c)
    return by_expiry


def nearest_expiry(contracts: list) -> str | None:
    """
    Pick the expiry with the smallest days-to-expiry.

    NOTE: the original Flask code (`_current_spot_and_contracts` and
    `api_strategy_template`) tried to do this with
    `hasattr(contracts[0], 'days_to_expiry')` guards that silently fell
    back to a constant key (effectively `min()` over an unordered set,
    i.e. an arbitrary/non-deterministic pick) whenever that attribute was
    missing. This version always derives days-to-expiry the same way
    `contract_to_api` does (from `expiry_g`), so the result is deterministic
    regardless of what the Contract class happens to expose.
    """
    from datetime import date

    if not contracts:
        return None
    dated = [c for c in contracts if getattr(c, "expiry_g", None)]
    if not dated:
        # nothing has a parsed gregorian date — fall back to first expiry seen
        return contracts[0].expiry_j
    nearest = min(dated, key=lambda c: (c.expiry_g - date.today()).days)
    return nearest.expiry_j
