"""GET /api/v1/rooms/{room_id}/activity — per-room activity timeline (#427)."""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import ActivityLog, Agent, Base, Room, User


@pytest_asyncio.fixture()
async def env(config: AnygardenSettings):
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        admin = User(email="admin-act@anygarden.io", password_hash="x", is_admin=True)
        member = User(email="member-act@anygarden.io", password_hash="x")
        agent = Agent(name="A", engine="codex")
        room = Room(name="roomA")
        other = Room(name="roomB")
        db.add_all([admin, member, agent, room, other])
        await db.flush()
        # roomA: a full turn; roomB: one unrelated row.
        db.add_all([
            ActivityLog(agent_id=agent.id, event_type="message_received",
                        request_id="rq", room_id=room.id, details={"room_id": room.id}),
            ActivityLog(agent_id=agent.id, event_type="handler_started",
                        request_id="rq", room_id=room.id, details={"room_id": room.id}),
            ActivityLog(agent_id=agent.id, event_type="handler_finished",
                        request_id="rq", room_id=room.id,
                        details={"room_id": room.id, "outcome": "ok"}),
            ActivityLog(agent_id=agent.id, event_type="message_received",
                        request_id="other", room_id=other.id, details={"room_id": other.id}),
        ])
        await db.commit()
        admin_token = create_user_token(admin.id, admin.email, True, secret=config.jwt_secret)
        member_token = create_user_token(member.id, member.email, False, secret=config.jwt_secret)
        room_id = room.id

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    yield {"app": app, "room_id": room_id, "admin": admin_token, "member": member_token}
    await engine.dispose()


async def test_room_activity_returns_only_that_rooms_rows(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            f"/api/v1/rooms/{env['room_id']}/activity",
            headers={"Authorization": f"Bearer {env['admin']}"},
        )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 3  # roomA's three rows, not roomB's
    assert {row["event_type"] for row in rows} == {
        "message_received", "handler_started", "handler_finished",
    }
    # newest first
    assert rows[0]["timestamp"] >= rows[-1]["timestamp"]


async def test_room_activity_is_admin_gated(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            f"/api/v1/rooms/{env['room_id']}/activity",
            headers={"Authorization": f"Bearer {env['member']}"},
        )
    assert r.status_code == 403
