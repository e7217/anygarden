"""Creation acknowledgements and capacity under real multi-connection races."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from anygarden.auth.jwt import create_user_token
from anygarden.db.models import Agent, Machine, Room
from anygarden.scheduler import placement
from sqlalchemy import func, select

from .test_agents_api import agents_env as shared_agents_env

agents_env = shared_agents_env


async def create(env, body, token=None):
    return await env["client"].post(
        "/api/v1/agents", json={"engine": "echo", "name": "Reviewer", **body},
        headers={"Authorization": f"Bearer {token or env['token']}"},
    )


@pytest.mark.asyncio
async def test_committed_creation_survives_dispatch_failure_and_retry(agents_env):
    env = agents_env
    env["lifecycle"]._build_sync_frame = AsyncMock(side_effect=RuntimeError("test dispatch failure"))
    body = {"request_id": str(uuid4()), "machine_id": env["machine"].id}
    first = await create(env, body)
    second = await create(env, body)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["unavailable_reason"]["code"] == "spawn_failed"
    assert first.json()["placed_on_machine_id"] == env["machine"].id
    async with env["factory"]() as db:
        assert await db.scalar(select(func.count()).select_from(Agent)) == 1
        assert await db.scalar(select(func.count()).select_from(Room).where(Room.is_dm.is_(True))) == 1
    assert env["lifecycle"]._build_sync_frame.await_count == 1


@pytest.mark.asyncio
async def test_request_key_rejects_changed_payload_without_creating_a_duplicate(agents_env):
    key = str(uuid4())
    first = await create(agents_env, {"request_id": key, "permission_level": "restricted"})
    changed = await create(agents_env, {"request_id": key, "permission_level": "trusted"})
    assert first.status_code == 201
    assert changed.status_code == 409
    assert "different settings" in changed.json()["detail"]
    async with agents_env["factory"]() as db:
        rows = (await db.scalars(select(Agent))).all()
        assert len(rows) == 1
        assert rows[0].permission_level == "restricted"


@pytest.mark.asyncio
async def test_request_key_is_scoped_to_the_authenticated_creator(agents_env):
    env = agents_env
    async with env["factory"]() as db:
        user = await db.get(type(env["regular"]), env["regular"].id)
        user.is_admin = True
        await db.commit()
    # Auth verifies the signed admin identity against the persisted user.
    token = create_user_token(env["regular"].id, env["regular"].email, True,
                              secret=env["app"].state.config.jwt_secret)
    body = {"request_id": str(uuid4())}
    first = await create(env, body)
    second = await create(env, body, token)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "starting", "running", "stopping"])
async def test_each_live_or_reserved_state_occupies_capacity(agents_env, state):
    env = agents_env
    async with env["factory"]() as db:
        machine = await db.get(Machine, env["machine"].id)
        machine.max_agents = 1
        db.add(Agent(name="occupant", engine="echo", actual_state=state, placed_on_machine_id=machine.id))
        await db.commit()
    response = await create(env, {"machine_id": env["machine"].id})
    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [False, True])
@pytest.mark.parametrize("agents_env", ["file"], indirect=True)
async def test_concurrent_pinned_creates_serialize_the_last_slot(agents_env, monkeypatch, same_key):
    env = agents_env
    async with env["factory"]() as db:
        machine = await db.get(Machine, env["machine"].id)
        machine.max_agents = 1
        await db.commit()
    # Force both independent DB sessions to see a free slot before either
    # takes the writer lock. This exercises SQLite read -> write contention.
    original = placement.select_machine_for
    ready = asyncio.Event()
    reads = 0

    async def barrier(*args, **kwargs):
        nonlocal reads
        result = await original(*args, **kwargs)
        reads += 1
        if reads <= 2:
            if reads == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 5)
        return result

    monkeypatch.setattr(placement, "select_machine_for", barrier)
    key = str(uuid4())
    responses = await asyncio.wait_for(asyncio.gather(
        create(env, {"machine_id": env["machine"].id, "request_id": key}),
        create(env, {"machine_id": env["machine"].id, "request_id": key if same_key else str(uuid4())}),
    ), 10)
    assert sorted(r.status_code for r in responses) == ([201, 201] if same_key else [201, 409])
    if same_key:
        assert responses[0].json()["id"] == responses[1].json()["id"]
    async with env["factory"]() as db:
        assert await db.scalar(select(func.count()).select_from(Agent)) == 1
        assert await db.scalar(select(func.count()).select_from(Room)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("agents_env", ["file"], indirect=True)
async def test_concurrent_automatic_placement_reserves_only_one_process(agents_env):
    env = agents_env
    async with env["factory"]() as db:
        machine = await db.get(Machine, env["machine"].id)
        machine.max_agents = 1
        await db.commit()
    responses = await asyncio.gather(create(env, {}), create(env, {}))
    assert [r.status_code for r in responses] == [201, 201]
    async with env["factory"]() as db:
        rows = (await db.scalars(select(Agent))).all()
        assert len(rows) == 2  # auto placement can queue an unplaced agent
        assert sum(row.placed_on_machine_id is not None for row in rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("model,effort,expected", [
    ("gpt-6-luna", "ultra", 422), ("gpt-6-sol", "ultra", 201),
    (None, "minimal", 422), ("custom-gateway-model", "high", 201),
])
async def test_creation_checks_model_reasoning_but_accepts_custom_models(agents_env, model, effort, expected):
    response = await create(agents_env, {"engine": "codex-cli", "model": model, "reasoning_effort": effort})
    assert response.status_code == expected


@pytest.mark.asyncio
async def test_model_update_requires_clearing_an_incompatible_reasoning_value(agents_env):
    env = agents_env
    response = await create(env, {"engine": "codex-cli", "model": "gpt-6-sol", "reasoning_effort": "ultra"})
    agent_id = response.json()["id"]
    headers = {"Authorization": f"Bearer {env['token']}"}
    rejected = await env["client"].put(f"/api/v1/agents/{agent_id}", headers=headers,
        json={"model": "gpt-6-luna", "model_set": True})
    assert rejected.status_code == 422
    accepted = await env["client"].put(f"/api/v1/agents/{agent_id}", headers=headers,
        json={"model": "gpt-6-luna", "model_set": True, "reasoning_effort": None, "reasoning_effort_set": True})
    assert accepted.status_code == 200
    assert accepted.json()["model"] == "gpt-6-luna"
    assert accepted.json()["reasoning_effort"] is None
