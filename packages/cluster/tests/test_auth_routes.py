"""Tests for /api/v1/auth endpoints."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.scheduler.lifecycle import AgentLifecycle


@pytest_asyncio.fixture()
async def auth_env():
    """Self-contained fixture: in-memory DB, app with state, async client."""
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.machine_bus = bus
    app.state.agent_lifecycle = lifecycle

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await engine.dispose()


# ── Registration ─────────────────────────────────────────────────────


async def test_register_success(auth_env: AsyncClient):
    resp = await auth_env.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "s3cret!"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "user_id" in data
    assert "token" in data


async def test_register_duplicate_email(auth_env: AsyncClient):
    payload = {"email": "alice@example.com", "password": "s3cret!"}
    await auth_env.post("/api/v1/auth/register", json=payload)
    resp = await auth_env.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409


async def test_register_first_user_is_admin(auth_env: AsyncClient):
    resp = await auth_env.post(
        "/api/v1/auth/register",
        json={"email": "first@example.com", "password": "s3cret!"},
    )
    assert resp.status_code == 201
    token = resp.json()["token"]

    me_resp = await auth_env.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["is_admin"] is True


async def test_register_second_user_not_admin(auth_env: AsyncClient):
    # First user (admin)
    await auth_env.post(
        "/api/v1/auth/register",
        json={"email": "first@example.com", "password": "s3cret!"},
    )

    # Second user (not admin)
    resp = await auth_env.post(
        "/api/v1/auth/register",
        json={"email": "second@example.com", "password": "password2"},
    )
    assert resp.status_code == 201
    token = resp.json()["token"]

    me_resp = await auth_env.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["is_admin"] is False


# ── Login ────────────────────────────────────────────────────────────


async def test_login_success(auth_env: AsyncClient):
    payload = {"email": "login@example.com", "password": "s3cret!"}
    await auth_env.post("/api/v1/auth/register", json=payload)
    resp = await auth_env.post("/api/v1/auth/login", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["user"]["email"] == "login@example.com"


async def test_login_wrong_password(auth_env: AsyncClient):
    payload = {"email": "wrong-pw@example.com", "password": "s3cret!"}
    await auth_env.post("/api/v1/auth/register", json=payload)
    resp = await auth_env.post(
        "/api/v1/auth/login",
        json={"email": "wrong-pw@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401


async def test_login_nonexistent_email(auth_env: AsyncClient):
    resp = await auth_env.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401


# ── /me ──────────────────────────────────────────────────────────────


async def test_me_authenticated(auth_env: AsyncClient):
    reg = await auth_env.post(
        "/api/v1/auth/register",
        json={"email": "me@example.com", "password": "s3cret!"},
    )
    token = reg.json()["token"]

    resp = await auth_env.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "me@example.com"
    assert "id" in data
    assert "is_admin" in data


async def test_me_unauthenticated(auth_env: AsyncClient):
    resp = await auth_env.get("/api/v1/auth/me")
    assert resp.status_code == 401
