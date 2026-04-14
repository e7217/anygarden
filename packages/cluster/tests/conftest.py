"""Shared pytest fixtures — in-memory DB, session, test app, and client."""

from __future__ import annotations

import secrets
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.app import create_app
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Base


@pytest.fixture()
def config() -> DooraeSettings:
    """Return a test configuration with an in-memory SQLite DB."""
    return DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )


@pytest_asyncio.fixture()
async def engine(config: DooraeSettings):
    eng = build_engine(config.db_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def db(engine) -> AsyncIterator[AsyncSession]:
    factory = build_session_factory(engine)
    async with factory() as session:
        yield session


@pytest.fixture()
def app(config: DooraeSettings):
    """Return a FastAPI test application."""
    application = create_app(config)
    return application


@pytest_asyncio.fixture()
async def client(app) -> AsyncIterator[AsyncClient]:
    """HTTPX async client wired to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
