"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def build_engine(db_url: str) -> AsyncEngine:
    """Create an async engine for the given database URL.

    For SQLite, enable ``PRAGMA foreign_keys=ON`` on every connection so
    that ON DELETE CASCADE / SET NULL actually fire — without this, SQLite
    silently ignores foreign-key constraints.
    """
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_async_engine(db_url, echo=False, connect_args=connect_args)

    if db_url.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def _fk_pragma_on_connect(dbapi_conn, _connection_record):  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to *engine*."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
