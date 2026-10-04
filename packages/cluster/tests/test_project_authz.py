"""Project visibility and deletion follow creator and room membership (#783).

ROOM-01 — a project is visible to a global admin, its creator and the
participants of its rooms; only the creator or a global admin may delete
it. Projects without a recorded creator are deletable by admins only.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Participant, Project, Room, User

pytestmark = pytest.mark.req("ROOM-01")


@pytest_asyncio.fixture()
async def env(config: AnygardenSettings) -> AsyncIterator[dict]:
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessions() as db:
        users = {
            name: User(email=f"{name}@anygarden.io", password_hash="x", is_admin=name == "admin")
            for name in ("creator", "member", "outsider", "admin")
        }
        db.add_all(users.values())
        await db.flush()
        # Pre-082 project: no recorded creator, ``member`` is in one of its rooms.
        legacy = Project(name="legacy")
        db.add(legacy)
        await db.flush()
        legacy_room = Room(project_id=legacy.id, name="legacy-room")
        db.add(legacy_room)
        await db.flush()
        db.add(Participant(room_id=legacy_room.id, user_id=users["member"].id, role="member"))
        await db.commit()
        for obj in (*users.values(), legacy):
            await db.refresh(obj)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "sessions": sessions,
            "users": users,
            "legacy": legacy,
            "tokens": {
                name: create_user_token(u.id, u.email, u.is_admin, secret=config.jwt_secret)
                for name, u in users.items()
            },
        }
    await engine.dispose()


def _auth(env, who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {env['tokens'][who]}"}


async def _create(env, who: str, name: str) -> dict:
    resp = await env["client"].post(
        "/api/v1/projects", json={"name": name}, headers=_auth(env, who)
    )
    assert resp.status_code == 201
    return resp.json()


async def _names(env, who: str) -> set[str]:
    resp = await env["client"].get("/api/v1/projects", headers=_auth(env, who))
    assert resp.status_code == 200
    return {p["name"] for p in resp.json()}


async def _join_room(env, project_id: str, who: str) -> None:
    async with env["sessions"]() as db:
        room = Room(project_id=project_id, name=f"room-{who}")
        db.add(room)
        await db.flush()
        db.add(Participant(room_id=room.id, user_id=env["users"][who].id, role="member"))
        await db.commit()


async def test_creator_sees_own_empty_project_and_outsiders_do_not(env):
    created = await _create(env, "creator", "alpha")

    assert created["created_by"] == env["users"]["creator"].id
    assert created["can_delete"] is True
    assert "alpha" in await _names(env, "creator")
    assert "alpha" not in await _names(env, "outsider")


async def test_room_members_and_admins_see_project(env):
    project = await _create(env, "creator", "alpha")
    await _join_room(env, project["id"], "member")

    assert "alpha" in await _names(env, "member")
    assert {"alpha", "legacy"} <= await _names(env, "admin")
    assert await _names(env, "outsider") == set()


async def test_can_delete_flag_reflects_caller(env):
    project = await _create(env, "creator", "alpha")
    await _join_room(env, project["id"], "member")

    listed = await env["client"].get("/api/v1/projects", headers=_auth(env, "member"))
    flags = {p["name"]: p["can_delete"] for p in listed.json()}
    assert flags == {"alpha": False, "legacy": False}


async def test_member_cannot_delete_and_outsider_gets_404(env):
    project = await _create(env, "creator", "alpha")
    await _join_room(env, project["id"], "member")
    url = f"/api/v1/projects/{project['id']}"

    assert (await env["client"].delete(url, headers=_auth(env, "member"))).status_code == 403
    assert (await env["client"].delete(url, headers=_auth(env, "outsider"))).status_code == 404

    async with env["sessions"]() as db:
        assert await db.get(Project, project["id"]) is not None


async def test_creator_and_admin_can_delete(env):
    mine = await _create(env, "creator", "alpha")
    theirs = await _create(env, "member", "beta")

    resp = await env["client"].delete(
        f"/api/v1/projects/{mine['id']}", headers=_auth(env, "creator")
    )
    assert resp.status_code == 204
    resp = await env["client"].delete(
        f"/api/v1/projects/{theirs['id']}", headers=_auth(env, "admin")
    )
    assert resp.status_code == 204

    async with env["sessions"]() as db:
        remaining = (await db.execute(select(Project.name))).scalars().all()
    assert remaining == ["legacy"]


async def test_project_without_creator_is_deletable_by_admin_only(env):
    url = f"/api/v1/projects/{env['legacy'].id}"

    assert (await env["client"].delete(url, headers=_auth(env, "member"))).status_code == 403
    assert (await env["client"].delete(url, headers=_auth(env, "admin"))).status_code == 204
