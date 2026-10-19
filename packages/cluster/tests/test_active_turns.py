"""MSG-09 — the sidebar learns which accessible rooms an agent is answering in."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from anygarden.db.models import AgentTurn, ProjectExecution, Room
from anygarden.db.repository import append_message
from anygarden.turns.service import create_turn

from .test_durable_turns import turn_env as turn_env
from .test_turn_cancel import _client

pytestmark = [pytest.mark.asyncio, pytest.mark.req("MSG-09")]


async def _turn_in_state(env, state: str) -> str:
    async with env["factory"]() as db:
        trigger = await append_message(db, env["room"], env["user_participant"], "hello")
        turn = await create_turn(
            db, room_id=env["room"], participant_id=env["agent_participant"],
            agent_id=env["agent"], trigger_message_id=trigger.id,
        )
        turn.state = state
        request_id = turn.request_id
        await db.commit()
    return request_id


async def _active(env, who: str = "member"):
    client, headers = await _client(env, None)
    async with client:
        return await client.get("/api/v1/turns/active", headers=headers[who])


@pytest.mark.parametrize("state", ["leased", "retrying", "completing"])
async def test_working_turn_marks_its_room_and_agent(turn_env, state) -> None:
    await _turn_in_state(turn_env, state)
    response = await _active(turn_env)
    assert response.status_code == 200
    assert response.json() == [{"room_id": turn_env["room"], "agent_ids": [turn_env["agent"]]}]


@pytest.mark.parametrize("state", ["pending", "completed", "cancelled", "failed"])
async def test_queued_or_finished_turns_are_not_reported(turn_env, state) -> None:
    await _turn_in_state(turn_env, state)
    response = await _active(turn_env)
    assert response.status_code == 200
    assert response.json() == []


async def test_agent_is_listed_once_per_room(turn_env) -> None:
    await _turn_in_state(turn_env, "leased")
    await _turn_in_state(turn_env, "completing")
    response = await _active(turn_env)
    assert response.json() == [{"room_id": turn_env["room"], "agent_ids": [turn_env["agent"]]}]


async def test_rooms_the_caller_cannot_access_are_not_reported(turn_env) -> None:
    await _turn_in_state(turn_env, "leased")
    response = await _active(turn_env, "outsider")
    assert response.status_code == 200
    assert response.json() == []


async def test_execution_bound_turns_count_as_working(turn_env) -> None:
    request_id = await _turn_in_state(turn_env, "leased")
    async with turn_env["factory"]() as db:
        turn = await db.scalar(select(AgentTurn).where(AgentTurn.request_id == request_id))
        room = await db.get(Room, turn_env["room"])
        execution = ProjectExecution(
            project_id=room.project_id, operating_room_id=room.id,
            lead_agent_id=turn_env["agent"], source_message_id=turn.trigger_message_id,
            objective="ship it",
        )
        db.add(execution)
        await db.flush()
        turn.execution_id = execution.id
        await db.commit()
    response = await _active(turn_env)
    assert response.json() == [{"room_id": turn_env["room"], "agent_ids": [turn_env["agent"]]}]


async def test_requires_authentication(turn_env) -> None:
    client, _ = await _client(turn_env, None)
    async with client:
        response = await client.get("/api/v1/turns/active")
    assert response.status_code == 401
