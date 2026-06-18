"""Activity endpoints filtered by the first-class outcome/engine columns (#447).

``outcome`` and ``engine`` were promoted out of the ``details`` JSON to
indexed columns. Both ``/agents/{id}/activity`` and
``/rooms/{id}/activity`` expose them as response fields and accept them as
optional query filters; an absent filter must preserve the legacy
(unfiltered) behaviour.
"""

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
        admin = User(email="admin-oc@anygarden.io", password_hash="x", is_admin=True)
        agent = Agent(name="A", engine="codex")
        room = Room(name="roomA")
        db.add_all([admin, agent, room])
        await db.flush()
        # Two terminal turns with distinct outcomes/engines plus a
        # system event with neither, all in the same room/agent.
        db.add_all([
            ActivityLog(
                agent_id=agent.id, event_type="handler_finished",
                request_id="rq-ok", room_id=room.id,
                outcome="ok", engine="codex",
                details={"room_id": room.id, "outcome": "ok", "engine": "codex"},
            ),
            ActivityLog(
                agent_id=agent.id, event_type="handler_finished",
                request_id="rq-to", room_id=room.id,
                outcome="timeout", engine="claude",
                details={"room_id": room.id, "outcome": "timeout", "engine": "claude"},
            ),
            ActivityLog(
                agent_id=agent.id, event_type="start_requested",
                request_id=None, room_id=None,
                outcome=None, engine=None, details=None,
            ),
        ])
        await db.commit()
        token = create_user_token(admin.id, admin.email, True, secret=config.jwt_secret)
        agent_id = agent.id
        room_id = room.id

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    yield {
        "app": app,
        "agent_id": agent_id,
        "room_id": room_id,
        "token": token,
    }
    await engine.dispose()


async def test_agent_activity_filters_by_outcome(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            f"/api/v1/agents/{env['agent_id']}/activity",
            params={"outcome": "timeout"},
            headers={"Authorization": f"Bearer {env['token']}"},
        )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["request_id"] == "rq-to"
    assert rows[0]["outcome"] == "timeout"
    assert rows[0]["engine"] == "claude"


async def test_agent_activity_filters_by_engine(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            f"/api/v1/agents/{env['agent_id']}/activity",
            params={"engine": "codex"},
            headers={"Authorization": f"Bearer {env['token']}"},
        )
    assert r.status_code == 200
    rows = r.json()
    assert [row["request_id"] for row in rows] == ["rq-ok"]
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["engine"] == "codex"


async def test_agent_activity_unfiltered_returns_all_and_exposes_columns(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            f"/api/v1/agents/{env['agent_id']}/activity",
            headers={"Authorization": f"Bearer {env['token']}"},
        )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 3  # filter absent → legacy behaviour preserved
    by_req = {row.get("request_id"): row for row in rows}
    assert by_req["rq-ok"]["outcome"] == "ok"
    assert by_req["rq-ok"]["engine"] == "codex"
    # system event has null columns
    assert by_req[None]["outcome"] is None
    assert by_req[None]["engine"] is None


async def test_room_activity_filters_by_outcome(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            f"/api/v1/rooms/{env['room_id']}/activity",
            params={"outcome": "ok"},
            headers={"Authorization": f"Bearer {env['token']}"},
        )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["request_id"] == "rq-ok"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["engine"] == "codex"
