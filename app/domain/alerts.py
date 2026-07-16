"""
app/domain/alerts.py
======================
Ported verbatim from lotus_server.py. Kept as plain tuples internally
(tag, color, msg) to avoid touching the Persian message formatting, then
converted to the API shape via `alerts_to_api`.
"""
from __future__ import annotations

from typing import List, Tuple

Alert = Tuple[str, str, str]  # (tag, color, msg)

_TAG_LEVELS = {
    "خرید؟": "warn", "گران": "warn",
    "حجم↑": "info", "کم‌نقد": "critical",
    "یک‌طرفه": "critical", "OI!": "info",
}


def compute_alerts_persian(contracts, history) -> List[Alert]:
    alerts: List[Alert] = []
    for c in contracts:
        # Mispricing
        if c.last > 0 and c.settle > 0:
            disc = (c.settle - c.last) / c.settle
            if disc >= 0.08:
                alerts.append(("خرید؟", "yellow",
                               f"{c.code} — آخرین {c.last:,.0f} ریال، "
                               f"{abs(disc) * 100:.0f}٪ پایین‌تر از تسویه {c.settle:,.0f} ریال."))
            elif disc <= -0.08:
                alerts.append(("گران", "magenta",
                               f"{c.code} — آخرین {c.last:,.0f} ریال، "
                               f"{abs(disc) * 100:.0f}٪ بالاتر از تسویه {c.settle:,.0f} ریال."))
        # Volume spike
        fac = history.vol_spike_factor(c.code, c.volume)
        if fac and fac >= 5 and c.volume > 0:
            alerts.append(("حجم↑", "cyan",
                           f"{c.code} — حجم {c.volume:,} لات، {fac:.1f}× میانگین اخیر."))
        # Thin
        if 0 < c.volume < 10:
            alerts.append(("کم‌نقد", "red",
                           f"{c.code} — فقط {c.volume} لات. نقدشوندگی بسیار پایین."))
        # One-sided
        if c.volume > 0 and (c.demand == 0 or c.supply == 0):
            side = "فروش‌محور" if c.demand == 0 else "خریدمحور"
            alerts.append(("یک‌طرفه", "red", f"{c.code} — جریان {side}."))
        # OI jump
        if c.oi > 0 and abs(c.d_oi) >= 0.5 * c.oi and abs(c.d_oi) > 1000:
            direction = "افزایش" if c.d_oi > 0 else "کاهش"
            alerts.append(("OI!", "green" if c.d_oi > 0 else "blue",
                           f"{c.code} — {direction} OI: {c.d_oi:+,} روی {c.oi:,}."))
    return alerts


def alerts_to_api(alerts: List[Alert]) -> list[dict]:
    return [{
        "tag": tag,
        "level": _TAG_LEVELS.get(tag, "info"),
        "msg": msg,
        "code": msg.split("—")[0].strip() if "—" in msg else "",
    } for tag, _color, msg in alerts]
