"""API-level guards for goal exactly-once + stampede caps (#449).

- Run-now twice in the same minute on a *manual* goal → idempotent
  (one Task, second call returns 200 not 409/500).
- Per-owner active-goal cap → the (cap+1)th create returns 422.

These exercise the real router (auth, IntegrityError handling, the
cap query) end-to-end against the in-memory SQLite app.
"""

from __future__ import annotations

import secrets
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from anygarden.api.v1.goals import MAX_ACTIVE_GOALS_PER_OWNER
from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, Base, Goal, Participant, Room, Task, User


@pytest_asyncio.fixture()
async def goals_env() -> AsyncIterator[dict]:
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        owner = User(email="owner@test.com", password_hash="x", is_admin=True)
        db.add(owner)
        await db.flush()
        agent = Agent(name="bot", engine="echo")
        db.add(agent)
        await db.flush()
        room = Room(name="r")
        db.add(room)
        await db.flush()
        owner_p = Participant(room_id=room.id, user_id=owner.id, role="member")
        agent_p = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add_all([owner_p, agent_p])
        await db.commit()
        owner_id, owner_email, owner_admin = owner.id, owner.email, owner.is_admin
        agent_id = agent.id
        room_id = room.id

    token = create_user_token(
        owner_id, owner_email, owner_admin, secret=config.jwt_secret
    )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "token": token,
            "agent_id": agent_id,
            "room_id": room_id,
            "factory": factory,
        }
    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_manual_run_twice_same_minute_is_idempotent(goals_env):
    client = goals_env["client"]
    agent_id = goals_env["agent_id"]
    room_id = goals_env["room_id"]
    factory = goals_env["factory"]

    # A manual goal (no auto-schedule → next_run_at is NULL).
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/goals",
        json={
            "title": "run-once",
            "spec": "do it",
            "trigger_type": "manual",
            "trigger_config": {},
            "materialize": "full",
            "report_room_id": room_id,
        },
        headers=_auth(goals_env["token"]),
    )
    assert resp.status_code == 201, resp.text
    goal_id = resp.json()["id"]

    r1 = await client.post(
        f"/api/v1/goals/{goal_id}/run", headers=_auth(goals_env["token"])
    )
    r2 = await client.post(
        f"/api/v1/goals/{goal_id}/run", headers=_auth(goals_env["token"])
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    # Exactly one Task despite two Run-now calls in the same minute.
    async with factory() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Task)
                .where(Task.goal_id == goal_id)
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_per_owner_active_goal_cap_rejects(goals_env):
    """Creating goals up to the cap succeeds; the next one is 422.

    We seed cap-1 active goals directly (fast), then drive the last
    two through the API so the cap query runs end-to-end."""
    client = goals_env["client"]
    agent_id = goals_env["agent_id"]
    room_id = goals_env["room_id"]
    factory = goals_env["factory"]

    # Resolve the owner id behind the token via the first created goal.
    first = await client.post(
        f"/api/v1/agents/{agent_id}/goals",
        json={
            "title": "g0",
            "spec": "s",
            "trigger_type": "manual",
            "trigger_config": {},
            "report_room_id": room_id,
        },
        headers=_auth(goals_env["token"]),
    )
    assert first.status_code == 201, first.text
    owner_id = first.json()["owner_id"]

    # Seed active goals up to one short of the cap (we already made 1).
    async with factory() as db:
        for i in range(MAX_ACTIVE_GOALS_PER_OWNER - 2):
            db.add(
                Goal(
                    assignee_agent_id=agent_id,
                    owner_id=owner_id,
                    report_room_id=room_id,
                    title=f"seed-{i}",
                    spec="s",
                    status="active",
                    trigger_type="manual",
                    trigger_config={},
                    materialize="full",
                )
            )
        await db.commit()
        active = (
            await db.execute(
                select(func.count())
                .select_from(Goal)
                .where(Goal.owner_id == owner_id, Goal.status == "active")
            )
        ).scalar_one()
    assert active == MAX_ACTIVE_GOALS_PER_OWNER - 1

    # The (cap)th create still succeeds.
    ok = await client.post(
        f"/api/v1/agents/{agent_id}/goals",
        json={
            "title": "at-cap",
            "spec": "s",
            "trigger_type": "manual",
            "trigger_config": {},
            "report_room_id": room_id,
        },
        headers=_auth(goals_env["token"]),
    )
    assert ok.status_code == 201, ok.text

    # The (cap+1)th is rejected.
    rejected = await client.post(
        f"/api/v1/agents/{agent_id}/goals",
        json={
            "title": "over-cap",
            "spec": "s",
            "trigger_type": "manual",
            "trigger_config": {},
            "report_room_id": room_id,
        },
        headers=_auth(goals_env["token"]),
    )
    assert rejected.status_code == 422, rejected.text
    assert "active goal limit" in rejected.json()["detail"]
