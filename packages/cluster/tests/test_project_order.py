"""Tests for per-user sidebar project order (#995).

``PUT /api/v1/projects/order`` stores the caller's order from a full
snapshot; ``GET /api/v1/projects`` returns ordered projects first and
the rest by creation time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Base,
    Participant,
    Project,
    Room,
    User,
    UserProjectOrder,
)
from anygarden.ws.protocol import ProjectOrderChangedOut


@pytest_asyncio.fixture()
async def order_env(config: AnygardenSettings):
    """A user and a second user who both see projects p0..p2, plus a
    project only the second user can see."""
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with session_factory() as db:
        user = User(email="order-user@anygarden.io", password_hash="x")
        other = User(email="order-other@anygarden.io", password_hash="x")
        db.add_all([user, other])
        await db.flush()

        projects: list[Project] = []
        for i in range(3):
            p = Project(
                name=f"p{i}", created_by=user.id, created_at=base + timedelta(minutes=i)
            )
            db.add(p)
            await db.flush()
            room = Room(project_id=p.id, name=f"r{i}")
            db.add(room)
            await db.flush()
            db.add(Participant(room_id=room.id, user_id=other.id, role="member"))
            projects.append(p)
        hidden = Project(
            name="hidden", created_by=other.id, created_at=base + timedelta(minutes=9)
        )
        db.add(hidden)
        await db.commit()
        project_ids = [p.id for p in projects]
        hidden_id = hidden.id
        user_id, other_id = user.id, other.id

    def token(uid: str, email: str) -> str:
        return create_user_token(uid, email, False, secret=config.jwt_secret)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = session_factory

    yield {
        "app": app,
        "session_factory": session_factory,
        "project_ids": project_ids,
        "hidden_id": hidden_id,
        "user_id": user_id,
        "user_headers": {
            "Authorization": f"Bearer {token(user_id, 'order-user@anygarden.io')}"
        },
        "other_headers": {
            "Authorization": f"Bearer {token(other_id, 'order-other@anygarden.io')}"
        },
    }
    await engine.dispose()


async def _list_ids(client: AsyncClient, headers: dict) -> list[str]:
    resp = await client.get("/api/v1/projects", headers=headers)
    assert resp.status_code == 200
    return [p["id"] for p in resp.json()]


@pytest.mark.req("ROOM-14")
@pytest.mark.asyncio
async def test_saved_order_comes_back_and_unordered_projects_follow(order_env) -> None:
    p0, p1, p2 = order_env["project_ids"]
    headers = order_env["user_headers"]
    async with AsyncClient(
        transport=ASGITransport(app=order_env["app"]), base_url="http://test"
    ) as client:
        assert await _list_ids(client, headers) == [p0, p1, p2]

        resp = await client.put(
            "/api/v1/projects/order", json={"project_ids": [p2, p0]}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json() == {"project_ids": [p2, p0, p1]}

        # p1 has no saved position, so it follows the ordered ones.
        assert await _list_ids(client, headers) == [p2, p0, p1]


@pytest.mark.req("ROOM-14")
@pytest.mark.asyncio
async def test_order_is_per_user(order_env) -> None:
    p0, p1, p2 = order_env["project_ids"]
    async with AsyncClient(
        transport=ASGITransport(app=order_env["app"]), base_url="http://test"
    ) as client:
        await client.put(
            "/api/v1/projects/order",
            json={"project_ids": [p2, p1, p0]},
            headers=order_env["user_headers"],
        )
        assert await _list_ids(client, order_env["other_headers"]) == [
            p0,
            p1,
            p2,
            order_env["hidden_id"],
        ]


@pytest.mark.asyncio
async def test_projects_the_caller_cannot_see_are_ignored(order_env) -> None:
    p0, p1, p2 = order_env["project_ids"]
    hidden = order_env["hidden_id"]
    async with AsyncClient(
        transport=ASGITransport(app=order_env["app"]), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/v1/projects/order",
            json={"project_ids": [hidden, "no-such-project", p1]},
            headers=order_env["user_headers"],
        )
        assert resp.status_code == 200
        assert resp.json() == {"project_ids": [p1, p0, p2]}

    async with order_env["session_factory"]() as db:
        rows = (await db.execute(select(UserProjectOrder))).scalars().all()
        assert [r.project_id for r in rows] == [p1]


@pytest.mark.asyncio
async def test_partial_snapshot_keeps_existing_positions(order_env) -> None:
    p0, p1, p2 = order_env["project_ids"]
    headers = order_env["user_headers"]
    async with AsyncClient(
        transport=ASGITransport(app=order_env["app"]), base_url="http://test"
    ) as client:
        await client.put(
            "/api/v1/projects/order", json={"project_ids": [p2, p1, p0]}, headers=headers
        )
        await client.put(
            "/api/v1/projects/order", json={"project_ids": [p1]}, headers=headers
        )
    async with order_env["session_factory"]() as db:
        rows = (
            await db.execute(
                select(UserProjectOrder).where(
                    UserProjectOrder.user_id == order_env["user_id"]
                )
            )
        ).scalars().all()
        assert {r.project_id for r in rows} == {p0, p1, p2}


@pytest.mark.asyncio
async def test_deleting_a_project_removes_its_position(order_env) -> None:
    p0, p1, p2 = order_env["project_ids"]
    headers = order_env["user_headers"]
    async with AsyncClient(
        transport=ASGITransport(app=order_env["app"]), base_url="http://test"
    ) as client:
        await client.put(
            "/api/v1/projects/order", json={"project_ids": [p2, p1, p0]}, headers=headers
        )
        resp = await client.delete(f"/api/v1/projects/{p1}", headers=headers)
        assert resp.status_code == 204
        assert await _list_ids(client, headers) == [p2, p0]
    async with order_env["session_factory"]() as db:
        rows = (await db.execute(select(UserProjectOrder))).scalars().all()
        assert {r.project_id for r in rows} == {p0, p2}


@pytest.mark.req("ROOM-14")
@pytest.mark.asyncio
async def test_new_order_reaches_the_users_other_sessions(order_env) -> None:
    class _RecordingManager:
        def __init__(self) -> None:
            self.sent: list[tuple[str, object]] = []

        async def send_to(self, pid: str, frame: object) -> bool:
            self.sent.append((pid, frame))
            return True

    # The user needs a room participation to have live sessions.
    async with order_env["session_factory"]() as db:
        room = (await db.execute(select(Room).limit(1))).scalar_one()
        part = Participant(room_id=room.id, user_id=order_env["user_id"], role="member")
        db.add(part)
        await db.commit()
        part_id = part.id

    manager = _RecordingManager()
    order_env["app"].state.connection_manager = manager
    p0, p1, p2 = order_env["project_ids"]
    async with AsyncClient(
        transport=ASGITransport(app=order_env["app"]), base_url="http://test"
    ) as client:
        await client.put(
            "/api/v1/projects/order",
            json={"project_ids": [p2, p1, p0]},
            headers=order_env["user_headers"],
        )
    assert [pid for pid, _ in manager.sent] == [part_id]
    frame = manager.sent[0][1]
    assert isinstance(frame, ProjectOrderChangedOut)
    assert frame.user_id == order_env["user_id"]
    assert frame.project_ids == [p2, p1, p0]
