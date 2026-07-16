"""
app/core/exceptions.py
========================
The Flask app returned errors as `jsonify({"error": ...}), 400` scattered
inline in route bodies, and one route (portfolio summary) even swallowed
exceptions and returned a fabricated 200-OK payload with an "error" key
buried inside it — a client checking status codes would never notice.

Here every domain error is a typed exception, raised where it happens, and
translated to a proper HTTP response in exactly one place.
"""
from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("lotus.errors")


class LotusError(Exception):
    """Base class for all domain errors. `status_code` drives the HTTP response."""
    status_code = 400

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class FundNotFoundError(LotusError):
    status_code = 400


class MarketDataUnavailableError(LotusError):
    status_code = 503


class PositionNotFoundError(LotusError):
    status_code = 404


class StrategyNotFoundError(LotusError):
    status_code = 404


class InvalidStrategyTypeError(LotusError):
    status_code = 400


class InvalidRequestError(LotusError):
    status_code = 400


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(LotusError)
    async def handle_lotus_error(request: Request, exc: LotusError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        # The old Flask code logged unhandled errors inconsistently (some
        # routes had try/except + traceback.format_exc(), most didn't, and
        # an uncaught exception fell through to Flask's default HTML 500
        # page — awkward for a JSON API). This is the single catch-all.
        log.error("Unhandled error on %s %s: %s\n%s",
                   request.method, request.url.path, exc, traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": "خطای داخلی سرور — Internal server error"},
        )
