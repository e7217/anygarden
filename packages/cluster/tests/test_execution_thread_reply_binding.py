"""The owner's thread answer wakes the execution lead in a bound turn (#994)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from anygarden.db.models import (
    AgentTurn,
    ExecutionInputRevision,
    Participant,
    ProjectExecution,
    Task,
)
from tests.test_general_task_thread_answer import _user_send
from tests.test_mcp_ask_peer import _seed_sender_and_peer
from tests.test_ws_handler import (
    ws_env as ws_env,  # noqa: PLC0414 — pytest fixture re-export
)


@pytest.fixture(autouse=True)
def _no_recovery_loop(monkeypatch) -> None:
    monkeypatch.setenv("ANYGARDEN_TURN_RECOVERY_INTERVAL_SEC", "0")


async def _seed_execution(sf, room, lead_pid: str, source_message_id: str) -> tuple[str, str]:
    async with sf() as db:
        lead_agent_id = (await db.get(Participant, lead_pid)).agent_id
        root = Task(
            room_id=room.id, title="Ship it", status="in_progress",
            assignee_participant_id=lead_pid, triggered_by="project_execution",
        )
        db.add(root)
        await db.flush()
        execution = ProjectExecution(
            project_id=room.project_id, operating_room_id=room.id,
            lead_agent_id=lead_agent_id, source_message_id=source_message_id,
            root_task_id=root.id, objective="ship it", status="waiting_children",
        )
        db.add(execution)
        await db.flush()
        root.execution_id = execution.id
        root.input_revision = 1
        db.add(ExecutionInputRevision(
            execution_id=execution.id, revision=1, objective="ship it",
            user_constraints="ship it", source_message_id=source_message_id,
            root_task_id=root.id, change_scope="all",
        ))
        await db.commit()
        return execution.id, root.id


async def _turn_for(sf, message_id: str, participant_id: str) -> AgentTurn:
    async with sf() as db:
        return await db.scalar(select(AgentTurn).where(
            AgentTurn.trigger_message_id == message_id,
            AgentTurn.target_participant_id == participant_id,
        ))


@pytest.mark.req("EXE-25")
@pytest.mark.asyncio
async def test_owner_thread_answer_binds_the_lead_turn_to_the_execution(ws_env) -> None:
    app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
    _, lead_pid, peer_pid = await _seed_sender_and_peer(sf, room)
    with TestClient(app) as client:
        source = _user_send(client, room.id, ws_env["token"], f"<@user:{lead_pid}> 릴리스 준비해줘")
        execution_id, root_id = await _seed_execution(sf, room, lead_pid, source["id"])
        answer = _user_send(
            client, room.id, ws_env["token"],
            f"<@user:{lead_pid}> <@user:{peer_pid}> v2.4 태그 기준으로 해줘",
            thread_root_id=source["id"],
        )
        elsewhere = _user_send(client, room.id, ws_env["token"], f"<@user:{lead_pid}> 다른 얘기")

    lead_turn = await _turn_for(sf, answer["id"], lead_pid)
    assert (lead_turn.task_id, lead_turn.execution_id) == (root_id, execution_id)
    assert lead_turn.execution_input_revision == 1
    assert lead_turn.state == "pending"
    # Only the lead is bound; everyone else, and every other place, is untouched.
    peer_turn = await _turn_for(sf, answer["id"], peer_pid)
    assert (peer_turn.task_id, peer_turn.execution_id, peer_turn.state) == (None, None, "pending")
    main_turn = await _turn_for(sf, elsewhere["id"], lead_pid)
    assert (main_turn.task_id, main_turn.execution_id, main_turn.state) == (None, None, "pending")
