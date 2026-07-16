"""
app/db/session.py
===================
Replaces `LotusDB`/`PortfolioDB`'s `@contextmanager def _conn(self)` —
each of those opened a fresh `sqlite3.connect(...)`, set PRAGMAs, and
closed it per call. The SQLAlchemy equivalent: one `AsyncEngine` per
database (created once, in `main.py`'s lifespan), and each repository
method opens a short-lived `AsyncSession` from a shared
`async_sessionmaker`, mirroring the same "connection lives only as long
as the call" discipline — see the docstrings in `repositories/market_repo.py`
and `repositories/portfolio_repo.py`.

PRAGMAs (`journal_mode=WAL`, `foreign_keys=ON`) are applied via a
`connect` event listener instead of being re-issued in every call, since
the underlying DBAPI connection is now pooled by SQLAlchemy rather than
opened fresh each time.
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(db_url: str, *, echo: bool = False) -> AsyncEngine:
    engine = create_async_engine(db_url, echo=echo)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: repository methods return dicts built from
    # ORM objects immediately after commit; without this, touching an
    # attribute after commit would trigger a lazy refresh (and, since the
    # session is about to close, an error).
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine, base) -> None:
    """Equivalent of the old `conn.executescript(SCHEMA)` — creates every
    table registered on `base.metadata` if it doesn't already exist."""
    async with engine.begin() as conn:
        await conn.run_sync(base.metadata.create_all)
