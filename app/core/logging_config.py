"""
app/core/logging_config.py
===========================
The legacy code used a bespoke `make_logger()` from lotus_monitor for
business-logic logging (fetch cycles, persist errors, etc). We keep using
that function for those call sites unchanged, but configure Python's
standard `logging` module too, so uvicorn access/error logs, FastAPI
exception logs, and any `logging.getLogger(__name__)` calls in the new
code share one consistent format instead of print()-based ad hoc output.
"""
import logging
import sys

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level)

    # Avoid duplicate handlers on reload
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)

    # Quiet down noisy third-party loggers unless we're debugging
    if settings.log_level.upper() != "DEBUG":
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
