"""DELETE /api/v1/agents/{id} contract (AGT-06).

Recorded invocation history blocks deletion with 409; otherwise the
delete stops the agent, evicts its token cache and removes its DM.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    Participant,
    Room,
)
from anygarden.db.native_invocation_models import NativeInvocationAccounting

from .test_agents_api import agents_env as agents_env

pytestmark = [pytest.mark.asyncio, pytest.mark.req("AGT-06")]


async def _create(env, name: str) -> str:
    response = await env["client"].post(
        "/api/v1/agents",
        json={"engine": "echo", "name": name},
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _dm_room_id(env, agent_id: str) -> str | None:
    async with env["factory"]() as db:
        return await db.scalar(
            select(Room.id)
            .join(Participant, Participant.room_id == Room.id)
            .where(Participant.agent_id == agent_id, Room.is_dm.is_(True))
        )


async def test_delete_refused_when_invocation_history_exists(agents_env) -> None:
    env = agents_env
    agent_id = await _create(env, "accounted")
    room_id = await _dm_room_id(env, agent_id)
    async with env["factory"]() as db:
        participant_id = await db.scalar(
            select(Participant.id).where(
                Participant.agent_id == agent_id, Participant.room_id == room_id
            )
        )
        db.add(AgentTurn(
            request_id="turn-1", room_id=room_id,
            target_participant_id=participant_id, agent_id=agent_id,
            idempotency_key="k1", state="completed",
        ))
        await db.flush()
        attempt = AgentTurnAttempt(
            turn_id="turn-1", agent_id=agent_id, attempt_number=1,
            generation=0, lease_token="lease-1", state="started",
        )
        db.add(attempt)
        await db.flush()
        db.add(NativeInvocationAccounting(
            attempt_id=attempt.id, local_execution_id="local-1",
            request_id="turn-1", agent_id=agent_id, room_id=room_id,
            attempt_number=1, generation=0,
        ))
        await db.commit()

    lifecycle = env["lifecycle"]
    lifecycle.request_stop = AsyncMock()
    response = await env["client"].delete(
        f"/api/v1/agents/{agent_id}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "NATIVE_INVOCATION_HISTORY_DELETE_FORBIDDEN"
    lifecycle.request_stop.assert_not_called()
    async with env["factory"]() as db:
        assert await db.get(Agent, agent_id) is not None
    assert await _dm_room_id(env, agent_id) == room_id


async def test_delete_stops_evicts_token_and_removes_dm(agents_env) -> None:
    env = agents_env
    agent_id = await _create(env, "removable")
    room_id = await _dm_room_id(env, agent_id)
    assert room_id is not None
    async with env["factory"]() as db:
        agent = await db.get(Agent, agent_id)
        agent.actual_state = "running"
        await db.commit()

    lifecycle = env["lifecycle"]
    lifecycle.request_stop = AsyncMock()
    lifecycle.evict_token = MagicMock()
    response = await env["client"].delete(
        f"/api/v1/agents/{agent_id}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )

    assert response.status_code == 200, response.text
    lifecycle.request_stop.assert_awaited_once_with(agent_id)
    lifecycle.evict_token.assert_called_once_with(agent_id)
    async with env["factory"]() as db:
        assert await db.get(Agent, agent_id) is None
        assert await db.get(Room, room_id) is None
