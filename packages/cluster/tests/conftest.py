"""Shared pytest fixtures — in-memory DB, session, test app, and client."""

from __future__ import annotations

import copy
import os
import secrets
from typing import AsyncIterator

import pytest
import pytest_asyncio
import structlog
from fastapi import FastAPI
from fastapi.routing import (
    APIRoute,
    APIRouter,
    APIWebSocketRoute,
    _merge_lifespan_context,
    get_websocket_app,
    request_response,
    websocket_session,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import BaseRoute

from anygarden.app import create_app
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.fts import create_message_fts
from anygarden.db.models import Base

# SYS-06 — never let a test app start the background PyPI update check.
os.environ.setdefault("ANYGARDEN_UPDATE_CHECK_INTERVAL_HOURS", "0")


@event.listens_for(Engine, "connect")
def _skip_sqlite_fsync(dbapi_connection, _connection_record) -> None:
    """Don't wait for the disk on test SQLite DBs (#749).

    File-backed test DBs (``tmp_path / "x.db"``) otherwise sync after
    every DDL statement, so one ``create_all`` takes ~1.5 s instead of
    ~0.05 s. Test DBs are thrown away, so durability does not matter.
    Covers aiosqlite and pysqlite engines, including alembic's.
    """
    if "sqlite" not in type(dbapi_connection).__module__:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA synchronous=OFF")
    cursor.close()


_included_routes: dict[int, tuple[APIRouter, int, list[BaseRoute]]] = {}
_fastapi_include_router = FastAPI.include_router


def _include_router_once(app: FastAPI, router: APIRouter, **options) -> None:
    """Build each module router's routes once per worker, not once per app (#1001).

    ``create_app`` runs for most tests (~1,300 per suite run). Each run
    rebuilt all ~250 routes: FastAPI's ``include_router`` re-analyses every
    endpoint's dependencies and regenerates its pydantic response schema,
    about a quarter of the suite's time. None of that depends on the app.
    The one app-specific part is the request handler, which looks
    ``dependency_overrides`` up on the app it was built for. So copy the
    routes built for the first app and give each copy a handler bound to
    the new app.

    Startup hooks and lifespans are merged as ``include_router`` does.
    Includes with options (prefix, tags, ...) take the normal path.
    """
    if options:
        _fastapi_include_router(app, router, **options)
        return
    cached = _included_routes.get(id(router))
    if cached is None or cached[0] is not router or cached[1] != len(router.routes):
        start = len(app.router.routes)
        _fastapi_include_router(app, router)
        _included_routes[id(router)] = (router, len(router.routes), app.router.routes[start:])
        return
    for route in cached[2]:
        if isinstance(route, APIRoute):
            route = copy.copy(route)
            route.dependency_overrides_provider = app
            route.app = request_response(route.get_route_handler())
        elif isinstance(route, APIWebSocketRoute):
            route = copy.copy(route)
            route.app = websocket_session(
                get_websocket_app(
                    dependant=route.dependant,
                    dependency_overrides_provider=app,
                    embed_body_fields=route._embed_body_fields,
                )
            )
        app.router.routes.append(route)
    app.router.on_startup.extend(router.on_startup)
    app.router.on_shutdown.extend(router.on_shutdown)
    app.router.lifespan_context = _merge_lifespan_context(
        app.router.lifespan_context, router.lifespan_context
    )


FastAPI.include_router = _include_router_once


@pytest.fixture(autouse=True)
def _reset_structlog():
    """Undo any app's ``configure_logging`` after each test.

    Booting an app runs ``structlog.configure`` process-wide, including a
    level filter (many tests boot with ``log_level="WARNING"``). Left in
    place, it silently drops INFO events for later tests in the same
    worker, so ``capture_logs`` assertions passed or failed by test order.
    """
    yield
    structlog.reset_defaults()


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


def pytest_collection_modifyitems(config, items):
    """Record ``@pytest.mark.req`` IDs as a JUnit ``req`` property (#781).

    ``scripts/req_report.py`` reads the property back from the JUnit XML
    to report each requirement's pass/fail state.
    """
    for item in items:
        ids = sorted({i for m in item.iter_markers("req") for i in m.args})
        if ids:
            item.user_properties.append(("req", ",".join(ids)))
