"""Shared pytest fixtures — in-memory DB, session, test app, and client."""

from __future__ import annotations

import secrets
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.app import create_app
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.fts import create_message_fts
from anygarden.db.models import Base


@pytest.fixture()
def config() -> AnygardenSettings:
    """Return a test configuration with an in-memory SQLite DB.

    ``mcp_secrets_key`` is pre-populated with a freshly generated
    Fernet key so #124's encryption layer stays happy during boot
    without every test having to spell it out. Tests that need to
    exercise the missing-key path should construct their own
    ``AnygardenSettings`` without this key.
    """
    from cryptography.fernet import Fernet
    return AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
    )


@pytest_asyncio.fixture()
async def engine(config: AnygardenSettings):
    eng = build_engine(config.db_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all omits the raw FTS5 index (it lives only in migration
        # 008), so mirror the fresh-DB bootstrap (#473) here to let search
        # endpoint integration tests run against a real messages_fts.
        if eng.dialect.name == "sqlite":
            await create_message_fts(conn)
    yield eng
    # Defensive (#464): swallow the in-memory aiosqlite teardown race
    # ("no active connection" during dispose()'s rollback) — the DB is
    # discarded and a cleanup race must not ERROR an otherwise-green test.
    try:
        await eng.dispose()
    except Exception:  # pragma: no cover — best-effort teardown cleanup
        pass


@pytest_asyncio.fixture()
async def db(engine) -> AsyncIterator[AsyncSession]:
    factory = build_session_factory(engine)
    async with factory() as session:
        yield session


@pytest.fixture()
def app(config: AnygardenSettings):
    """Return a FastAPI test application."""
    application = create_app(config)
    return application


@pytest_asyncio.fixture()
async def client(app) -> AsyncIterator[AsyncClient]:
    """HTTPX async client wired to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
