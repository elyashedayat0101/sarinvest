"""
lotus_monitor.py — Multi-fund Live Monitor, Alerts & Analysis
=============================================================

Supports multiple funds (Lotus, Kahroba, etc.) via fund_config.py.
Each fund is fetched, analyzed, and displayed independently.

Usage:
    python lotus_monitor.py                      # live dashboard (all funds)
    python lotus_monitor.py --fund lotus          # only Lotus
    python lotus_monitor.py --fund kahroba        # only Kahroba
    python lotus_monitor.py --interval 6          # custom refresh seconds
    python lotus_monitor.py --report-every 5      # regenerate report every N cycles
    python lotus_monitor.py --replay              # OFFLINE: drive from newest data CSV
    python lotus_monitor.py --once                # one fetch + one report, then exit
"""

import argparse
import json
import math
import os
import re
import statistics
import time
from collections import defaultdict, deque
from datetime import datetime, date
from pathlib import Path

import requests

try:
    import jdatetime
    HAS_JDATETIME = True
except ImportError:
    HAS_JDATETIME = False

import colorama
colorama.just_fix_windows_console()
from colorama import Fore, Style, Back

from .fund_config import FUNDS, FundConfig, MONTHS, get_fund, detect_fund, all_fund_ids


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
class Config:
    BASE_URL = "https://www.ime.co.ir/subsystems/ime/option/optionboarddata.ashx"
    MAIN_PAGE = "https://www.ime.co.ir/optionboard.html"
    LIMIT = 100
    LANG = 8
    TIMEOUT = 30
    MAX_RETRIES = 4
    OUTPUT_DIR = Path("lotus_data")
    REPORT_PATH = Path("lotus_analysis_report.md")
    HISTORY_LEN = 40
    RISK_FREE = 0.0


HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Referer": Config.MAIN_PAGE,
    "X-Requested-With": "XMLHttpRequest",
}


# --------------------------------------------------------------------------- #
# Persian helpers
# --------------------------------------------------------------------------- #
FA_DIGITS = {"۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5",
             "۶": "6", "۷": "7", "۸": "8", "۹": "9", "،": ",", "٫": "."}


def fa_to_en(text) -> str:
    text = str(text or "")
    for fa, en in FA_DIGITS.items():
        text = text.replace(fa, en)
    return text.strip()


def jalali_to_greg(js):
    if not js or not HAS_JDATETIME:
        return None
    try:
        y, m, d = (int(p) for p in fa_to_en(js).split("/"))
        return jdatetime.date(y, m, d).togregorian()
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Contract model — fund-aware
# --------------------------------------------------------------------------- #
class Contract:
    """One option contract's current snapshot, normalized from a raw ashx row."""
    __slots__ = ("code", "desc", "opt", "month", "strike", "last", "settle",
                 "prev_settle", "chg_pct", "volume", "value", "high", "low",
                 "oi", "d_oi", "demand", "supply", "buy_orders", "sell_orders",
                 "expiry_j", "expiry_g", "snapshot_dt", "fund_id")

    def __init__(self, raw: dict, fund: FundConfig = None):
        self.code = raw.get("ContractCode", "")

        # Auto-detect fund if not provided
        if fund is None:
            fund = detect_fund(self.code)
        self.fund_id = fund.fund_id if fund else "unknown"

        self.desc = raw.get("ContractDescription", "")
        self.opt = "C" if re.search(r"C\d+$", self.code) else "P"

        # Month label from code chars — position varies by fund
        if fund and len(self.code) >= 4:
            month_code = self.code[2:4]
            self.month = MONTHS.get(month_code, month_code)
        else:
            self.month = "?"

        # Strike: parse digits after C/P, multiply by fund's multiplier
        m = re.search(r"[CP](\d+)$", self.code)
        multiplier = fund.strike_multiplier if fund else 10000
        self.strike = int(m.group(1)) * multiplier if m else 0

        self.last = float(raw.get("LastPrice") or 0)
        self.settle = float(raw.get("TodaySettlementPrice") or 0)
        self.prev_settle = float(raw.get("LastSettlementPrice") or 0)
        self.chg_pct = float(raw.get("SettlementPricePercent") or 0)
        self.volume = int(raw.get("TradesVolume") or 0)
        self.value = float(raw.get("TradesValue") or 0)
        self.high = float(raw.get("MaxPrice") or 0)
        self.low = float(raw.get("MinPrice") or 0)
        self.oi = int(raw.get("OpenInterest") or 0)
        self.d_oi = int(raw.get("ChangeOpenInterest") or 0)
        self.demand = int(raw.get("Vol_Haghighi_Buy") or 0) + int(raw.get("Vol_Hoghooghi_Buy") or 0)
        self.supply = int(raw.get("Vol_Haghighi_Sell") or 0) + int(raw.get("Vol_Hoghooghi_Sell") or 0)
        self.buy_orders = int(raw.get("C_Buy") or 0)
        self.sell_orders = int(raw.get("C_Sell") or 0)
        self.expiry_j = raw.get("DeliveryDate", "")
        self.expiry_g = jalali_to_greg(self.expiry_j)
        self.snapshot_dt = raw.get("CreateDateTime", "")

    @property
    def ref_price(self):
        """Best usable price: last trade if it traded, else today's settlement."""
        return self.last if self.last > 0 else self.settle

    @property
    def years_to_expiry(self):
        if not self.expiry_g:
            return None
        days = (self.expiry_g - date.today()).days
        return max(days, 0) / 365.0

    @property
    def days_to_expiry(self):
        if not self.expiry_g:
            return None
        return max(0, (self.expiry_g - date.today()).days)

    @property
    def days_to_expiry_safe(self):
        d = self.days_to_expiry
        return d if d is not None else 999


# --------------------------------------------------------------------------- #
# Fetch layer — now fetches multiple funds
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, log, funds: list[FundConfig] = None):
        self.log = log
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._primed = False
        self.funds = funds or list(FUNDS.values())

    def _prime(self):
        """Load the main page to obtain fresh cookies / anti-bot token."""
        try:
            self.session.get(Config.MAIN_PAGE, timeout=Config.TIMEOUT)
            self._primed = True
            self.log("primed session (loaded main page for cookies)")
        except requests.RequestException as e:
            self.log(f"prime failed: {e}")

    def _params(self, offset, ot, fund: FundConfig):
        return {
            "f": fund.date_from,
            "t": fund.date_to,
            "c": -1,
            "ot": ot,
            "lang": Config.LANG,
            "search": fund.search_term,
            "order": "asc",
            "offset": offset,
            "limit": Config.LIMIT,
        }

    def _get(self, offset, ot, fund: FundConfig):
        for attempt in range(Config.MAX_RETRIES):
            try:
                r = self.session.get(Config.BASE_URL, params=self._params(offset, ot, fund),
                                     timeout=Config.TIMEOUT)
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                self.log(f"fetch attempt {attempt+1} failed (fund={fund.fund_id}, offset={offset}): {e}")
                self._primed = False
                self._prime()
                time.sleep(min(2 ** attempt, 8))
        return None

    @staticmethod
    def _rows(payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for k in ("rows", "data", "items", "result", "Data"):
                if isinstance(payload.get(k), list):
                    return payload[k]
            if isinstance(payload.get("d"), str):
                try:
                    return Fetcher._rows(json.loads(payload["d"]))
                except json.JSONDecodeError:
                    pass
        return []

    def _fetch_fund(self, fund: FundConfig) -> list:
        """Return list of raw dict rows for one fund."""
        rows, seen = [], set()
        for ot in (1, 2):
            offset = 0
            while True:
                r = self._get(offset, ot, fund)
                if r is None:
                    break
                try:
                    page = self._rows(r.json())
                except json.JSONDecodeError:
                    self.log(f"non-JSON response for {fund.fund_id}; skipping page")
                    break
                if not page:
                    break
                for raw in page:
                    code = raw.get("ContractCode")
                    if code and code.startswith(fund.code_prefix) and code not in seen:
                        seen.add(code)
                        rows.append(raw)
                if len(page) < Config.LIMIT:
                    break
                offset += Config.LIMIT
        return rows

    def fetch(self) -> dict[str, list]:
        """
        Fetch all configured funds.
        Returns: {fund_id: [raw_row_dicts]}
        """
        if not self._primed:
            self._prime()
        result = {}
        for fund in self.funds:
            rows = self._fetch_fund(fund)
            self.log(f"fetched {len(rows)} rows for {fund.fund_id}")
            result[fund.fund_id] = rows
        return result

    def fetch_single(self, fund_id: str) -> list:
        """Fetch a single fund. Returns list of raw rows."""
        fund = get_fund(fund_id)
        if not self._primed:
            self._prime()
        return self._fetch_fund(fund)


# --------------------------------------------------------------------------- #
# Rolling history for spike / OI-change detection
# --------------------------------------------------------------------------- #
class History:
    def __init__(self):
        self.vol = defaultdict(lambda: deque(maxlen=Config.HISTORY_LEN))
        self.oi = defaultdict(lambda: deque(maxlen=Config.HISTORY_LEN))

    def update(self, contracts):
        for c in contracts:
            self.vol[c.code].append(c.volume)
            self.oi[c.code].append(c.oi)

    def vol_spike_factor(self, code, current):
        hist = list(self.vol[code])[:-1]
        hist = [v for v in hist if v > 0]
        if len(hist) < 3:
            return None
        avg = statistics.mean(hist)
        return (current / avg) if avg > 0 else None


# --------------------------------------------------------------------------- #
# Black-Scholes (no scipy: erf-based normal CDF + bisection IV)
# --------------------------------------------------------------------------- #
def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S, K, T, sigma, is_call, r=Config.RISK_FREE):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def implied_vol(price, S, K, T, is_call):
    if price <= 0 or T <= 0 or S <= 0:
        return None
    intrinsic = max(0.0, (S - K) if is_call else (K - S))
    if price < intrinsic - 1e-6:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, mid, is_call) > price:
            hi = mid
        else:
            lo = mid
    iv = (lo + hi) / 2
    return iv if 1e-3 < iv < 4.99 else None


def estimate_spot(contracts):
    """Estimate underlying per expiry via put-call parity."""
    by_exp = defaultdict(dict)
    for c in contracts:
        p = c.ref_price
        if p > 0 and c.expiry_j:
            by_exp[c.expiry_j][(c.strike, c.opt)] = p
    spot = {}
    for exp, book in by_exp.items():
        ests = []
        strikes = {k for k, _ in book}
        for K in strikes:
            cp, pp = book.get((K, "C")), book.get((K, "P"))
            if cp and pp:
                ests.append(K + cp - pp)
        if ests:
            spot[exp] = statistics.median(ests)
        else:
            calls = [(K, book[(K, "C")]) for K in strikes if (K, "C") in book]
            if calls:
                K, cp = min(calls)
                spot[exp] = K + cp
    return spot


# --------------------------------------------------------------------------- #
# Persian Alerts
# --------------------------------------------------------------------------- #
def compute_alerts_persian(contracts, history):
    """Alert logic — fully in Persian."""
    alerts = []
    for c in contracts:
        # Mispricing
        if c.last > 0 and c.settle > 0:
            disc = (c.settle - c.last) / c.settle
            if disc >= 0.08:
                alerts.append(("خرید؟", "yellow",
                    f"{c.code} — آخرین قیمت {c.last:,.0f} ریال، "
                    f"{abs(disc)*100:.0f}٪ پایین‌تر از تسویه {c.settle:,.0f} ریال است. "
                    f"احتمال فرصت خرید ارزان یا قیمت‌گذاری نادرست."))
            elif disc <= -0.08:
                alerts.append(("گران", "magenta",
                    f"{c.code} — آخرین قیمت {c.last:,.0f} ریال، "
                    f"{abs(disc)*100:.0f}٪ بالاتر از تسویه {c.settle:,.0f} ریال است. "
                    f"قرارداد گران معامله می‌شود."))
        # Volume spike
        fac = history.vol_spike_factor(c.code, c.volume)
        if fac and fac >= 5 and c.volume > 0:
            alerts.append(("حجم↑", "cyan",
                f"{c.code} — حجم {c.volume:,} لات، "
                f"{fac:.1f} برابر میانگین اخیر. جهش فعالیت غیرعادی."))
        # Thin liquidity
        if 0 < c.volume < 10:
            alerts.append(("کم‌نقد", "red",
                f"{c.code} — تنها {c.volume} لات معامله شده. "
                f"نقدشوندگی بسیار پایین، احتمال لغزش قیمت بالاست."))
        # One-sided
        if c.volume > 0 and (c.demand == 0 or c.supply == 0):
            side = "فروش‌محور" if c.demand == 0 else "خریدمحور"
            alerts.append(("یک‌طرفه", "red",
                f"{c.code} — جریان معاملاتی {side} است. "
                f"اسپرد مؤثر احتمالاً بالاست."))
        # OI jump
        if c.oi > 0 and abs(c.d_oi) >= 0.5 * c.oi and abs(c.d_oi) > 1000:
            direction = "افزایش" if c.d_oi > 0 else "کاهش"
            alerts.append(("OI!", "green" if c.d_oi > 0 else "blue",
                f"{c.code} — {direction} موقعیت باز: "
                f"ΔOI {c.d_oi:+,} روی پایه {c.oi:,}. "
                f"{'ورود موقعیت‌های جدید' if c.d_oi > 0 else 'بسته‌شدن موقعیت‌های قبلی'}."))
    return alerts


def alerts_to_api(alerts: list) -> list:
    TAG_LEVELS = {
        "خرید؟": "warn", "گران": "warn",
        "حجم↑": "info", "کم‌نقد": "critical",
        "یک‌طرفه": "critical", "OI!": "info",
    }
    return [{
        "tag": tag,
        "level": TAG_LEVELS.get(tag, "info"),
        "msg": msg,
        "code": msg.split("—")[0].strip() if "—" in msg else "",
    } for tag, col, msg in alerts]


# --------------------------------------------------------------------------- #
# Persistence: append raw payloads to lotus_data/ JSONL stream
# --------------------------------------------------------------------------- #
def persist_raw(rows, fetch_ts, fund_id="lotus"):
    Config.OUTPUT_DIR.mkdir(exist_ok=True)
    stream = Config.OUTPUT_DIR / f"raw_stream_{fund_id}_{datetime.now().strftime('%Y%m%d')}.jsonl"
    with open(stream, "a", encoding="utf-8") as fh:
        for raw in rows:
            fh.write(json.dumps({"fetch_ts": fetch_ts, "fund": fund_id, **raw},
                                ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# Dashboard rendering
# --------------------------------------------------------------------------- #
COLOR_TAG = {"green": Fore.GREEN, "red": Fore.RED, "yellow": Fore.YELLOW,
             "cyan": Fore.CYAN, "magenta": Fore.MAGENTA, "blue": Fore.BLUE,
             "white": Fore.WHITE}


def fmt_num(n, width=0):
    if n is None or n == 0:
        s = "-"
    elif abs(n) >= 1000:
        s = f"{n:,.0f}"
    else:
        s = f"{n:,.0f}" if float(n).is_integer() else f"{n:,.1f}"
    return s.rjust(width) if width else s


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def render_dashboard(all_contracts_by_fund, all_alerts_by_fund, fetch_ts, cycle, live_ok, interval):
    clear_screen()
    now = datetime.now()
    age = (now - fetch_ts).total_seconds()
    status = (Back.GREEN + Fore.BLACK + " ● LIVE " if live_ok
              else Back.RED + Fore.WHITE + " ● STALE ") + Style.RESET_ALL

    total_contracts = sum(len(cs) for cs in all_contracts_by_fund.values())
    total_alerts = sum(len(als) for als in all_alerts_by_fund.values())

    print(f"{Style.BRIGHT}Multi-Fund Options Monitor{Style.RESET_ALL}"
          f"   {status}  updated {age:0.0f}s ago  ·  cycle #{cycle}  ·  "
          f"{total_contracts} contracts  ·  refresh {interval}s")
    print(Style.DIM + f"snapshot {fetch_ts:%Y-%m-%d %H:%M:%S}  ·  "
          f"Demand=تقاضا(buy vol)  Supply=عرضه(sell vol)  ·  prices in ریال" + Style.RESET_ALL)
    print()

    hdr = (f"{'Symbol':<14}{'T':<2}{'Strike':>10}{'Last':>11}{'Settle':>11}"
           f"{'Δ%':>8}{'Volume':>10}{'Demand':>9}{'Supply':>9}{'High':>11}"
           f"{'Low':>11}{'Value(mR)':>12}{'OI':>11}")

    for fund_id, contracts in all_contracts_by_fund.items():
        if not contracts:
            continue
        fund = FUNDS.get(fund_id)
        fund_label = fund.name_fa if fund else fund_id
        hi_vol = max((c.volume for c in contracts), default=0)
        vol_thresh = hi_vol * 0.5

        print(f"{Style.BRIGHT}{Fore.YELLOW}═══ {fund_label} ({fund_id.upper()}) ═══{Style.RESET_ALL}")
        print(Style.BRIGHT + hdr + Style.RESET_ALL)
        print("─" * len(hdr))

        for exp in sorted({c.expiry_j for c in contracts}):
            grp = sorted((c for c in contracts if c.expiry_j == exp),
                         key=lambda c: (c.opt, c.strike))
            gexp = grp[0].expiry_g.isoformat() if grp[0].expiry_g else exp
            print(Fore.CYAN + f"▼ سررسید {exp}  ({gexp})" + Style.RESET_ALL)
            for c in grp:
                tcol = Fore.GREEN if c.opt == "C" else Fore.RED
                chg_col = Fore.GREEN if c.chg_pct > 0 else (Fore.RED if c.chg_pct < 0 else Style.DIM)
                if c.last > 0 and c.settle > 0:
                    lcol = Fore.GREEN if c.last >= c.settle else Fore.RED
                else:
                    lcol = Style.DIM
                vol_style = Style.BRIGHT if c.volume >= vol_thresh and c.volume > 0 else ""
                line = (
                    f"{c.code:<14}"
                    f"{tcol}{c.opt:<2}{Style.RESET_ALL}"
                    f"{fmt_num(c.strike):>10}"
                    f"{lcol}{fmt_num(c.last):>11}{Style.RESET_ALL}"
                    f"{fmt_num(c.settle):>11}"
                    f"{chg_col}{c.chg_pct:>+7.2f}%{Style.RESET_ALL}"
                    f"{vol_style}{fmt_num(c.volume):>10}{Style.RESET_ALL}"
                    f"{Fore.GREEN}{fmt_num(c.demand):>9}{Style.RESET_ALL}"
                    f"{Fore.RED}{fmt_num(c.supply):>9}{Style.RESET_ALL}"
                    f"{fmt_num(c.high):>11}"
                    f"{fmt_num(c.low):>11}"
                    f"{fmt_num(c.value/1e6):>12}"
                    f"{fmt_num(c.oi):>11}"
                )
                print(line)
        print()

    # Alerts panel
    for fund_id, alerts in all_alerts_by_fund.items():
        if not alerts:
            continue
        fund = FUNDS.get(fund_id)
        fund_label = fund.name_fa if fund else fund_id
        print(Style.BRIGHT + Fore.YELLOW + f"⚠  ALERTS — {fund_label} ({len(alerts)})" + Style.RESET_ALL)
        for tag, col, msg in alerts[:15]:
            c = COLOR_TAG.get(col, Fore.WHITE)
            print(f"  {c}{Style.BRIGHT}[{tag:^6}]{Style.RESET_ALL} {msg}")
        if len(alerts) > 15:
            print(Style.DIM + f"  ... and {len(alerts)-15} more" + Style.RESET_ALL)
        print()

    if not any(all_alerts_by_fund.values()):
        print(Style.DIM + "no active alerts" + Style.RESET_ALL)

    print()
    print(Style.DIM + "Ctrl-C to stop  ·  report → lotus_analysis_report.md" + Style.RESET_ALL)


# --------------------------------------------------------------------------- #
# Analysis report (multi-fund)
# --------------------------------------------------------------------------- #
def build_report(all_contracts_by_fund, all_alerts_by_fund, fetch_ts, spot_override=None):
    lines = []
    A = lines.append
    A("# Multi-Fund Options — Analysis Report")
    A("")
    total = sum(len(cs) for cs in all_contracts_by_fund.values())
    fund_names = ", ".join(f.name_en for f in FUNDS.values() if f.fund_id in all_contracts_by_fund)
    A(f"_Generated {datetime.now():%Y-%m-%d %H:%M:%S} · snapshot {fetch_ts:%Y-%m-%d %H:%M:%S} · "
      f"{total} contracts across: {fund_names}_")
    A("")

    for fund_id, contracts in all_contracts_by_fund.items():
        if not contracts:
            continue
        fund = FUNDS.get(fund_id)
        alerts = all_alerts_by_fund.get(fund_id, [])
        A(f"---")
        A(f"## {fund.name_fa} ({fund.name_en})")
        A("")

        # Estimate spot
        if spot_override:
            spot = {c.expiry_j: spot_override for c in contracts}
        else:
            spot = estimate_spot(contracts)

        # Compute IV
        ivs = {}
        for c in contracts:
            S, T, p = spot.get(c.expiry_j), c.years_to_expiry, c.ref_price
            if S and T and p > 0:
                iv = implied_vol(p, S, c.strike, T, c.opt == "C")
                if iv:
                    ivs[c.code] = iv

        # Spot estimates
        if spot:
            A("### Estimated underlying (via put-call parity)")
            A("")
            A("| Expiry (سررسید) | Est. spot (ریال) |")
            A("|---|--:|")
            for exp in sorted(spot):
                A(f"| {exp} | {spot[exp]:,.0f} |")
            A("")

        # Top 5 by volume
        A("### Top 5 most active contracts")
        A("")
        A("| Rank | Symbol | Type | Strike | Volume | Value (mR) | Last | Settle | Δ% | OI |")
        A("|--:|---|:--:|--:|--:|--:|--:|--:|--:|--:|")
        for i, c in enumerate(sorted(contracts, key=lambda x: x.volume, reverse=True)[:5], 1):
            A(f"| {i} | {c.code} | {c.opt} | {c.strike:,} | {c.volume:,} | {c.value/1e6:,.0f} | "
              f"{c.last:,.0f} | {c.settle:,.0f} | {c.chg_pct:+.2f} | {c.oi:,} |")
        A("")

        # Call vs Put skew
        calls = [c for c in contracts if c.opt == "C"]
        puts = [c for c in contracts if c.opt == "P"]
        cvol, pvol = sum(c.volume for c in calls), sum(c.volume for c in puts)
        coi, poi = sum(c.oi for c in calls), sum(c.oi for c in puts)
        pcr_v = (pvol / cvol) if cvol else float("inf")
        A(f"- Call volume **{cvol:,}** vs Put volume **{pvol:,}** → P/C ratio **{pcr_v:.2f}**")
        A("")

        # Alerts
        if alerts:
            A("### Active alerts")
            A("")
            for tag, col, msg in alerts:
                A(f"- `{tag}` {msg}")
            A("")

    Config.REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


# --------------------------------------------------------------------------- #
# Logger
# --------------------------------------------------------------------------- #
def make_logger():
    def log(msg):
        ts = datetime.now().strftime("%H:%M:%S")
        with open("lotus_monitor.log", "a", encoding="utf-8") as fh:
            fh.write(f"{ts} {msg}\n")
    return log


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Multi-fund options live monitor")
    ap.add_argument("--interval", type=float, default=6.0, help="refresh seconds (5-8)")
    ap.add_argument("--report-every", type=int, default=5, help="regenerate report every N cycles")
    ap.add_argument("--replay", action="store_true", help="offline: drive from newest data CSV")
    ap.add_argument("--once", action="store_true", help="one fetch + report, then exit")
    ap.add_argument("--spot", type=float, default=None, help="override underlying NAV for IV")
    ap.add_argument("--fund", type=str, default=None,
                    help="only monitor specific fund (lotus, kahroba). Default: all funds")
    args = ap.parse_args()

    log = make_logger()

    # Determine which funds to monitor
    if args.fund:
        fund_list = [get_fund(args.fund)]
    else:
        fund_list = list(FUNDS.values())

    source = Fetcher(log, funds=fund_list)
    histories = {f.fund_id: History() for f in fund_list}
    cycle = 0

    try:
        while True:
            cycle += 1
            raw_by_fund = source.fetch()
            fetch_ts = datetime.now()
            live_ok = any(bool(rows) for rows in raw_by_fund.values())

            all_contracts = {}
            all_alerts = {}

            for fund_id, rows in raw_by_fund.items():
                if not rows:
                    all_contracts[fund_id] = []
                    all_alerts[fund_id] = []
                    continue

                fund = FUNDS[fund_id]
                persist_raw(rows, fetch_ts.isoformat(), fund_id)
                contracts = [Contract(r, fund) for r in rows]
                histories[fund_id].update(contracts)
                alerts = compute_alerts_persian(contracts, histories[fund_id])
                all_contracts[fund_id] = contracts
                all_alerts[fund_id] = alerts

            if live_ok:
                render_dashboard(all_contracts, all_alerts, fetch_ts, cycle,
                                 not args.replay, args.interval)
            else:
                clear_screen()
                print(Fore.RED + "No data this cycle (connection issue or empty feed). "
                      "Retrying…" + Style.RESET_ALL)
                log("empty fetch; retrying")

            if args.once or (cycle % max(args.report_every, 1) == 0):
                if live_ok:
                    n = build_report(all_contracts, all_alerts, fetch_ts, args.spot)
                    log(f"report written ({n} lines)")

            if args.once:
                if live_ok:
                    print(f"\nWrote {Config.REPORT_PATH}")
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(Style.RESET_ALL + "\nStopped. Report at " + str(Config.REPORT_PATH))


if __name__ == "__main__":
    main()
