"""
app/domains/crypto/exceptions.py
===================================
Domain-local exceptions, registered with the global handler the same way
`app/core/exceptions.py::LotusError` is (see router.py — it re-raises
these as `LotusError` subclasses so the existing global handler in
core/exceptions.py covers them too, without every domain needing its own
exception-handler registration).
"""
from __future__ import annotations

from app.core.exceptions import LotusError


class ExchangeUnavailableError(LotusError):
    """A single exchange failed (timeout, 5xx, malformed response). Callers
    should usually catch this per-exchange and degrade gracefully rather
    than letting it become an HTTP error — see service.py's fetch_all."""
    status_code = 502


class UnsupportedSymbolError(LotusError):
    status_code = 400


class AllExchangesUnavailableError(LotusError):
    """Every configured exchange failed for this request — unlike a single
    exchange failing (tolerated, partial results returned), this is the
    one case worth surfacing as a real error."""
    status_code = 503
