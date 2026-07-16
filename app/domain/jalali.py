"""
app/domain/jalali.py
======================
Ported verbatim from `portfolio_db.py`. These have nothing to do with
persistence — they're pure date-math/formatting — so they move to
`domain/` rather than `db/`, consistent with `options_math.py` and
`alerts.py` already living there. `PortfolioRepository` imports
`greg_to_jalali` from here instead of defining it inline.
"""
from __future__ import annotations

import math
from datetime import date


def greg_to_jalali(d) -> str:
    """Convert a gregorian date (or 'YYYY-MM-DD' string) to a Jalali
    string in Persian digits, e.g. '۱۴۰۵/۰۴/۱۴'."""
    if d is None:
        return ""
    if isinstance(d, str):
        try:
            from datetime import date as dt
            parts = d.split("-")
            d = dt(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            return d
    try:
        import jdatetime
        jd = jdatetime.date.fromgregorian(date=d)
        persian = f"{jd.year}/{jd.month:02d}/{jd.day:02d}"
        return _to_persian_digits(persian)
    except ImportError:
        return _greg_to_jalali_algo(d)


def _to_persian_digits(s: str) -> str:
    mapping = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
    return s.translate(mapping)


def _greg_to_jalali_algo(d) -> str:
    """Pure-Python Jalali conversion, used when `jdatetime` isn't installed."""
    jy, jm, jd_day = _jdn_to_jalali(_greg_to_jdn(d.year, d.month, d.day))
    return _to_persian_digits(f"{jy}/{jm:02d}/{jd_day:02d}")


def _greg_to_jdn(y, m, d):
    a = (14 - m) // 12
    y2 = y + 4800 - a
    m2 = m + 12 * a - 3
    return d + (153 * m2 + 2) // 5 + 365 * y2 + y2 // 4 - y2 // 100 + y2 // 400 - 32045


def _jdn_to_jalali(jdn):
    JALALI_EPOCH = 1948320
    j = jdn - JALALI_EPOCH
    j -= 1
    cycle, remainder = divmod(j, 1029983)
    if remainder == 1029982:
        ycycle = 2820
    else:
        aux1, aux2 = divmod(remainder, 366)
        ycycle = (2134 * aux1 + 2816 * aux2 + 2815) // 1028522 + aux1 + 1
    year = ycycle + 2820 * cycle + 474
    if year <= 0:
        year -= 1
    yday = jdn - (_jalali_to_jdn(year, 1, 1)) + 1
    if yday <= 186:
        month = math.ceil(yday / 31)
    else:
        month = math.ceil((yday - 6) / 30)
    day = jdn - _jalali_to_jdn(year, month, 1) + 1
    return year, month, day


def _jalali_to_jdn(jy, jm, jd):
    epbase = jy - (474 if jy >= 0 else 473)
    epyear = 474 + epbase % 2820
    return (jd +
            ((jm - 1) * 30 + min(jm - 1, 6)) +
            math.floor((epyear * 682 - 110) / 2816) +
            (epyear - 1) * 365 +
            math.floor(epbase / 2820) * 1029983 +
            1948319)


def today_jalali() -> str:
    return greg_to_jalali(date.today())


def format_jalali_full(s: str) -> str:
    """Format '۱۴۰۵/۰۴/۱۴' into '۱۴ تیر ۱۴۰۵'."""
    if not s:
        return s
    ascii_s = s.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789'))
    parts = ascii_s.replace('-', '/').split('/')
    if len(parts) != 3:
        return s
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return s

    month_names = [
        'فروردین', 'اردیبهشت', 'خرداد', 'تیر',
        'مرداد', 'شهریور', 'مهر', 'آبان',
        'آذر', 'دی', 'بهمن', 'اسفند'
    ]
    m_name = month_names[m - 1] if 1 <= m <= 12 else str(m)
    day_str = _to_persian_digits(str(d))
    year_str = _to_persian_digits(str(y))
    return f"{day_str} {m_name} {year_str}"
