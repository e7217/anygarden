"""Tests for /api/v1/projects endpoints."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from doorae.app import create_app
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Base
from doorae.scheduler.machine_bus import MachineBus
from doorae.scheduler.lifecycle import AgentLifecycle


@pytest_asyncio.fixture()
async def projects_env():
    """Self-contained fixture: in-memory DB, app with state, async client."""
    config = DooraeSettings(
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


async def _register_and_get_token(client: AsyncClient) -> str:
    """Helper: register a user and return the JWT."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "proj-user@example.com", "password": "s3cret!"},
    )
    assert resp.status_code == 201
    return resp.json()["token"]


async def test_create_project(projects_env: AsyncClient):
    token = await _register_and_get_token(projects_env)
    resp = await projects_env.post(
        "/api/v1/projects",
        json={"name": "My Project", "description": "A test project"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Project"
    assert data["description"] == "A test project"
    assert "id" in data


async def test_list_projects(projects_env: AsyncClient):
    token = await _register_and_get_token(projects_env)
    # Create two projects
    await projects_env.post(
        "/api/v1/projects",
        json={"name": "Project A"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await projects_env.post(
        "/api/v1/projects",
        json={"name": "Project B"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await projects_env.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "Project A"
    assert data[1]["name"] == "Project B"


async def test_create_project_unauthenticated(projects_env: AsyncClient):
    resp = await projects_env.post(
        "/api/v1/projects",
        json={"name": "No Auth"},
    )
    assert resp.status_code == 401
