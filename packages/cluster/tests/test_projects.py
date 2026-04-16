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


# ── Delete project ───────────────────────────────────────────────────


async def test_delete_empty_project(projects_env: AsyncClient):
    """A project with no rooms is removed by DELETE and disappears
    from the list endpoint."""
    token = await _register_and_get_token(projects_env)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await projects_env.post(
        "/api/v1/projects",
        json={"name": "Empty"},
        headers=headers,
    )
    project_id = resp.json()["id"]

    resp = await projects_env.delete(
        f"/api/v1/projects/{project_id}",
        headers=headers,
    )
    assert resp.status_code == 204

    resp = await projects_env.get("/api/v1/projects", headers=headers)
    assert resp.status_code == 200
    assert all(p["id"] != project_id for p in resp.json())


async def test_delete_project_with_rooms_cascades(projects_env: AsyncClient):
    """Deleting a project also removes its rooms via the
    ``ON DELETE CASCADE`` FK. GET on each room id returns 404."""
    token = await _register_and_get_token(projects_env)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await projects_env.post(
        "/api/v1/projects",
        json={"name": "With rooms"},
        headers=headers,
    )
    project_id = resp.json()["id"]

    room_ids: list[str] = []
    for name in ("r1", "r2"):
        rr = await projects_env.post(
            "/api/v1/rooms",
            json={"project_id": project_id, "name": name},
            headers=headers,
        )
        assert rr.status_code == 201
        room_ids.append(rr.json()["id"])

    resp = await projects_env.delete(
        f"/api/v1/projects/{project_id}",
        headers=headers,
    )
    assert resp.status_code == 204

    # Each room should be gone.
    for rid in room_ids:
        got = await projects_env.get(
            f"/api/v1/rooms/{rid}",
            headers=headers,
        )
        assert got.status_code == 404, f"room {rid} still present"


async def test_delete_project_not_found(projects_env: AsyncClient):
    token = await _register_and_get_token(projects_env)
    resp = await projects_env.delete(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_delete_project_unauthenticated(projects_env: AsyncClient):
    resp = await projects_env.delete(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000",
    )
    assert resp.status_code == 401
