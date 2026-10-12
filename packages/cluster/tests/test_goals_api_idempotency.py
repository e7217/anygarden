"""API-level guards for goal exactly-once + stampede caps (#449).

- Run-now twice in the same minute on a *manual* goal → idempotent
  (one Task, second call returns 200 not 409/500).
- Per-owner active-goal cap → the (cap+1)th create returns 422.

These exercise the real router (auth, IntegrityError handling, the
cap query) end-to-end against the in-memory SQLite app.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta, timezone
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
from anygarden.db.models import Agent, AgentToken, Base, Goal, Participant, Room, Task, User


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
@pytest.mark.parametrize("materialize", ["interesting_only", "full"])
async def test_completed_run_survives_replay_and_queue_cleanup(goals_env, materialize, monkeypatch):
    from anygarden.auth.token import generate_token, hash_agent_token

    env = goals_env
    client, headers = env["client"], _auth(env["token"])
    created = await client.post(
        f"/api/v1/agents/{env['agent_id']}/goals", headers=headers,
        json={"title": "Prepare draft", "spec": "Use confirmed facts only", "trigger_type": "manual",
              "trigger_config": {}, "materialize": materialize, "report_room_id": env["room_id"]},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    monkeypatch.setattr("anygarden.api.v1.goals._manual_run_idempotency_key", lambda goal, now: f"{goal.id}:test-slot")
    assert (await client.post(f"/api/v1/goals/{goal_id}/run", headers=headers)).status_code == 200
    agent_token = generate_token()
    digest, hint = hash_agent_token(agent_token)
    async with env["factory"]() as db:
        db.add(AgentToken(agent_id=env["agent_id"], token_hash=digest, lookup_hint=hint))
        task = (await db.scalars(select(Task).where(Task.goal_id == goal_id))).one()
        task_id = task.id
        await db.commit()
    for status in ("in_progress", "done", "done"):
        arguments = {"task_id": task_id, "status": status}
        if status == "done":
            arguments["result_markdown"] = "Verified draft; no external submission."
        response = await client.post("/mcp/rpc", headers=_auth(agent_token), json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "mark_task_status", "arguments": arguments},
        })
        assert not response.json()["result"]["isError"], response.text
    history_url = f"/api/v1/rooms/{env['room_id']}/tasks?goal_id={goal_id}"
    history = (await client.get(history_url, headers=headers)).json()
    assert len(history) == 1
    assert history[0]["result_markdown"] == "Verified draft; no external submission."
    assert history[0]["schedule_context"]["goal_id"] == goal_id
    if materialize == "full":
        # Clear the ordinary queue, retaining consumed run identity and evidence.
        cleared = await client.delete(f"/api/v1/agents/{env['agent_id']}/tasks?status=done", headers=headers)
        assert cleared.json()["deleted_count"] == 1
    assert (await client.get(f"/api/v1/rooms/{env['room_id']}/tasks", headers=headers)).json() == []
    # Explicit cleanup is also a visibility change, never a consumed-slot delete.
    assert (await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)).status_code == 200
    assert (await client.post(f"/api/v1/goals/{goal_id}/run", headers=headers)).status_code == 200
    restored = (await client.get(history_url, headers=headers)).json()
    assert len(restored) == 1 and restored[0]["id"] == task_id and restored[0]["is_silent"]


@pytest.mark.req("EXE-02")
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


@pytest.mark.asyncio
async def test_create_goal_rejects_archived_report_room(goals_env):
    async with goals_env["factory"]() as db:
        room = await db.get(Room, goals_env["room_id"])
        assert room is not None
        room.archived_at = datetime.now(timezone.utc)
        await db.commit()

    response = await goals_env["client"].post(
        f"/api/v1/agents/{goals_env['agent_id']}/goals",
        json={
            "title": "must not register",
            "spec": "must not run",
            "trigger_type": "manual",
            "trigger_config": {},
            "report_room_id": goals_env["room_id"],
        },
        headers=_auth(goals_env["token"]),
    )
    assert response.status_code == 409

    async with goals_env["factory"]() as db:
        goals = (await db.execute(select(Goal))).scalars().all()
        assert goals == []


async def _create_goal(goals_env, trigger_type: str, trigger_config: dict):
    return await goals_env["client"].post(
        f"/api/v1/agents/{goals_env['agent_id']}/goals",
        json={
            "title": "scheduled",
            "spec": "do it",
            "trigger_type": trigger_type,
            "trigger_config": trigger_config,
            "materialize": "full",
            "report_room_id": goals_env["room_id"],
        },
        headers=_auth(goals_env["token"]),
    )


async def _set_next_run(goals_env, goal_id: str, slot: datetime) -> None:
    async with goals_env["factory"]() as db:
        goal = await db.get(Goal, goal_id)
        goal.next_run_at = slot
        await db.commit()


@pytest.mark.req("EXE-02")
@pytest.mark.asyncio
async def test_redelivered_schedule_slot_returns_the_existing_task(goals_env):
    created = await _create_goal(goals_env, "interval", {"interval_seconds": 3600})
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    slot = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=5)
    await _set_next_run(goals_env, goal_id, slot)

    url = f"/api/v1/goals/{goal_id}/scheduled-events"
    body = {"scheduled_for": slot.isoformat()}
    first = await goals_env["client"].post(url, json=body, headers=_auth(goals_env["token"]))
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "accepted"
    task_id = first.json()["task_id"]
    assert task_id

    again = await goals_env["client"].post(url, json=body, headers=_auth(goals_env["token"]))
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "duplicate"
    assert again.json()["task_id"] == task_id
    async with goals_env["factory"]() as db:
        count = await db.scalar(
            select(func.count()).select_from(Task).where(Task.goal_id == goal_id)
        )
    assert count == 1


@pytest.mark.req("EXE-03")
@pytest.mark.asyncio
async def test_next_slot_waits_while_previous_run_is_open(goals_env):
    created = await _create_goal(goals_env, "interval", {"interval_seconds": 3600})
    goal_id = created.json()["id"]
    url = f"/api/v1/goals/{goal_id}/scheduled-events"
    first_slot = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    await _set_next_run(goals_env, goal_id, first_slot)
    first = await goals_env["client"].post(
        url, json={"scheduled_for": first_slot.isoformat()}, headers=_auth(goals_env["token"]))
    assert first.json()["status"] == "accepted", first.text

    # The previous run is still open (blocked waiting for input) when the
    # next slot comes due: it waits instead of overlapping.
    async with goals_env["factory"]() as db:
        task = await db.get(Task, first.json()["task_id"])
        task.status = "blocked"
        await db.commit()
    second_slot = first_slot + timedelta(seconds=5)
    await _set_next_run(goals_env, goal_id, second_slot)
    second = await goals_env["client"].post(
        url, json={"scheduled_for": second_slot.isoformat()}, headers=_auth(goals_env["token"]))
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "waiting_overlap"
    assert second.json()["task_id"] is None
    async with goals_env["factory"]() as db:
        count = await db.scalar(
            select(func.count()).select_from(Task).where(Task.goal_id == goal_id)
        )
    assert count == 1


@pytest.mark.req("EXE-05")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger_type,trigger_config",
    [
        ("interval", {"interval_seconds": 59}),
        ("cron", {"cron": "0 9 * * *", "timezone": "No/Such_Zone"}),
    ],
)
async def test_create_goal_rejects_short_interval_and_bad_timezone(
    goals_env, trigger_type, trigger_config
):
    resp = await _create_goal(goals_env, trigger_type, trigger_config)
    assert resp.status_code == 422, resp.text
    async with goals_env["factory"]() as db:
        assert await db.scalar(select(func.count()).select_from(Goal)) == 0
