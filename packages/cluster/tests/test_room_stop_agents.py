"""#772 — ``POST /rooms/{id}/stop-agents`` must stop every running agent.

The room endpoint used to leave a dirty ``desired_state`` write on the
request session while ``AgentLifecycle.request_stop`` wrote through its
own connection. On the second agent the autoflush took SQLite's write
lock first and ``request_stop`` failed with ``database is locked``.

These tests use a file-backed SQLite database on purpose: an in-memory
``StaticPool`` database shares one connection, so the lock inversion
cannot happen there (same lesson as #758).
"""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    ActivityLog,
    Agent,
    Base,
    Machine,
    Participant,
    Room,
    User,
)
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus


@pytest_asyncio.fixture()
async def stop_env(tmp_path):
    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'stop-agents.db'}",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    class FakeWS:
        async def send_text(self, data: str) -> None:
            pass

    async with factory() as db:
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        db.add(admin)
        await db.flush()
        machine = Machine(
            name="m",
            hostname="h",
            owner_user_id=admin.id,
            status="online",
            max_agents=10,
        )
        room = Room(name="dev")
        db.add_all([machine, room])
        await db.flush()
        db.add(Participant(room_id=room.id, user_id=admin.id, role="owner"))
        agent_ids = []
        for name in ("agent-a", "agent-b", "agent-c"):
            agent = Agent(
                name=name,
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
                pid=1234,
            )
            db.add(agent)
            await db.flush()
            db.add(Participant(room_id=room.id, agent_id=agent.id))
            agent_ids.append(agent.id)
        await db.commit()
        await bus.register(machine.id, FakeWS())
        token = create_user_token(
            admin.id, admin.email, admin.is_admin, secret=config.jwt_secret
        )
        room_id = room.id

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.machine_bus = bus
    app.state.agent_lifecycle = lifecycle

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "headers": {"Authorization": f"Bearer {token}"},
            "factory": factory,
            "room_id": room_id,
            "agent_ids": agent_ids,
            "lifecycle": lifecycle,
        }
    await engine.dispose()


async def _states(factory, agent_ids):
    async with factory() as db:
        rows = (
            await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
        ).scalars()
        return {a.id: (a.desired_state, a.actual_state) for a in rows}


@pytest.mark.asyncio
async def test_stop_agents_stops_every_running_agent(stop_env) -> None:
    resp = await stop_env["client"].post(
        f"/api/v1/rooms/{stop_env['room_id']}/stop-agents",
        headers=stop_env["headers"],
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["stopped"]) == sorted(stop_env["agent_ids"])
    assert body["failed"] == []
    assert body["count"] == 3

    states = await _states(stop_env["factory"], stop_env["agent_ids"])
    assert all(desired == "stopped" for desired, _ in states.values())
    assert all(actual == "stopping" for _, actual in states.values())
    async with stop_env["factory"]() as db:
        logs = (
            await db.execute(
                select(ActivityLog).where(ActivityLog.event_type == "stop_requested")
            )
        ).scalars().all()
    assert sorted(log.agent_id for log in logs) == sorted(stop_env["agent_ids"])


@pytest.mark.asyncio
async def test_stop_agents_reports_partial_failure(stop_env, monkeypatch) -> None:
    lifecycle = stop_env["lifecycle"]
    original = lifecycle.request_stop
    broken = stop_env["agent_ids"][1]

    async def flaky_request_stop(agent_id: str) -> None:
        if agent_id == broken:
            raise RuntimeError("boom")
        await original(agent_id)

    monkeypatch.setattr(lifecycle, "request_stop", flaky_request_stop)

    resp = await stop_env["client"].post(
        f"/api/v1/rooms/{stop_env['room_id']}/stop-agents",
        headers=stop_env["headers"],
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [f["id"] for f in body["failed"]] == [broken]
    assert sorted(body["stopped"]) == sorted(
        a for a in stop_env["agent_ids"] if a != broken
    )
    states = await _states(stop_env["factory"], stop_env["agent_ids"])
    assert states[broken] == ("running", "running")


@pytest.mark.asyncio
async def test_stop_agents_with_nothing_running(stop_env) -> None:
    client, headers = stop_env["client"], stop_env["headers"]
    first = await client.post(
        f"/api/v1/rooms/{stop_env['room_id']}/stop-agents", headers=headers
    )
    assert first.status_code == 200

    # Every agent is now "stopping"; a second click has nothing to do.
    second = await client.post(
        f"/api/v1/rooms/{stop_env['room_id']}/stop-agents", headers=headers
    )
    assert second.status_code == 200
    assert second.json() == {"stopped": [], "failed": [], "count": 0}
