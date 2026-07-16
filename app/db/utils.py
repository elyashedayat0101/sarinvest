"""
app/db/utils.py
=================
Replaces the `dict(r)` conversions the original code did on
`sqlite3.Row` results (enabled by `conn.row_factory = sqlite3.Row`).
Repository methods that return "a plain dict shaped like the DB row" —
which the routers/schemas downstream already expect, unchanged from the
original Flask API's response shapes — use this instead of hand-listing
every column.
"""
from __future__ import annotations

from typing import Any, Optional


def model_to_dict(obj: Optional[Any]) -> dict:
    if obj is None:
        return {}
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
