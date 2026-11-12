"""Per-agent alerts on the overview map (#1032).

Each agent shows what needs the person, across the rooms they can open:
a problem (a failed task, or its latest turn in a room failed), a request
waiting on a person, a finished answer the person has not read, and the
number of tasks queued for it. Nothing is stored: each alert goes away
when its cause does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from starlette.testclient import TestClient

from anygarden.db.models import Agent, AgentTurn, Message, Participant, Room, Task
from anygarden.db.workspace_action_models import WorkspaceActionRequest
from anygarden.orchestration.agent_alerts import agent_alerts
from tests.test_mcp_ask_peer import _seed_sender_and_peer
from tests.test_ws_handler import ws_env as ws_env

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
_seq = count(1)
_turn = count(1)


async def _agent_in(sf, room_id: str, name: str = "에이전트") -> tuple[str, str]:
    """An agent seated in the room; returns (agent_id, participant_id)."""
    async with sf() as db:
        agent = Agent(name=name, engine="codex", actual_state="running", desired_state="running")
        db.add(agent)
        await db.flush()
        participant = Participant(room_id=room_id, agent_id=agent.id, role="member")
        db.add(participant)
        await db.commit()
        return agent.id, participant.id


async def _task(sf, room_id: str, pid: str, status: str, *, silent: bool = False) -> str:
    async with sf() as db:
        task = Task(room_id=room_id, title=f"{status} 작업", status=status,
                    assignee_participant_id=pid, is_silent=silent)
        db.add(task)
        await db.commit()
        return task.id


async def _turn_row(sf, room_id: str, agent_id: str, state: str, *, minutes: int,
                    message_id: str | None = None, reason: str | None = None) -> None:
    async with sf() as db:
        db.add(AgentTurn(
            request_id=f"turn-{next(_turn)}", room_id=room_id, agent_id=agent_id,
            idempotency_key=f"key-{next(_turn)}", state=state, accepted_message_id=message_id,
            terminal_reason=reason, created_at=NOW + timedelta(minutes=minutes),
        ))
        await db.commit()


async def _message(sf, room_id: str, pid: str) -> tuple[str, int]:
    async with sf() as db:
        seq = next(_seq)
        message = Message(room_id=room_id, participant_id=pid, content="끝났습니다", seq=seq)
        db.add(message)
        await db.commit()
        return message.id, seq


async def _alerts(sf, room_ids, user_id, inbox_items=()):
    async with sf() as db:
        return await agent_alerts(db, room_ids=frozenset(room_ids), user_id=user_id,
                                  inbox_items=list(inbox_items), now=NOW)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestProblem:
    @pytest.mark.asyncio
    @pytest.mark.req("COL-20")
    async def test_failed_task_shows_until_it_is_requeued(self, ws_env) -> None:
        sf, room, user = ws_env["session_factory"], ws_env["room"], ws_env["user"]
        agent_id, pid = await _agent_in(sf, room.id)
        task_id = await _task(sf, room.id, pid, "failed")

        alerts = await _alerts(sf, {room.id}, user.id)
        assert [t["id"] for t in alerts[agent_id]["failed_tasks"]] == [task_id]

        async with sf() as db:
            (await db.get(Task, task_id)).status = "todo"
            await db.commit()
        alerts = await _alerts(sf, {room.id}, user.id)
        assert alerts[agent_id]["failed_tasks"] == []
        assert alerts[agent_id]["queued_tasks"] == 1

    @pytest.mark.asyncio
    @pytest.mark.req("COL-20")
    async def test_failed_turn_clears_when_the_next_turn_in_that_room_completes(self, ws_env) -> None:
        sf, room, user = ws_env["session_factory"], ws_env["room"], ws_env["user"]
        agent_id, _pid = await _agent_in(sf, room.id)
        await _turn_row(sf, room.id, agent_id, "failed", minutes=1)
        # A cancelled turn says nothing about whether the failure is fixed.
        await _turn_row(sf, room.id, agent_id, "cancelled", minutes=2)

        alerts = await _alerts(sf, {room.id}, user.id)
        assert alerts[agent_id]["failed_rooms"] == [room.id]

        await _turn_row(sf, room.id, agent_id, "completed", minutes=3)
        assert agent_id not in await _alerts(sf, {room.id}, user.id)

    @pytest.mark.asyncio
    async def test_silent_tasks_are_not_counted(self, ws_env) -> None:
        sf, room, user = ws_env["session_factory"], ws_env["room"], ws_env["user"]
        agent_id, pid = await _agent_in(sf, room.id)
        await _task(sf, room.id, pid, "failed", silent=True)
        await _task(sf, room.id, pid, "todo", silent=True)
        assert agent_id not in await _alerts(sf, {room.id}, user.id)


class TestWaiting:
    @pytest.mark.asyncio
    @pytest.mark.req("COL-20")
    async def test_pending_workspace_action_waits_until_it_expires(self, ws_env) -> None:
        sf, room, user = ws_env["session_factory"], ws_env["room"], ws_env["user"]
        agent_id, pid = await _agent_in(sf, room.id)
        async with sf() as db:
            db.add(WorkspaceActionRequest(
                agent_id=agent_id, agent_participant_id=pid, room_id=room.id, action="create_room",
                params={}, summary="새 방 만들기", status="pending", expires_at=NOW + timedelta(hours=1),
            ))
            db.add(WorkspaceActionRequest(
                agent_id=agent_id, agent_participant_id=pid, room_id=room.id, action="create_room",
                params={}, summary="지난 요청", status="pending", expires_at=NOW - timedelta(minutes=1),
            ))
            await db.commit()

        waiting = (await _alerts(sf, {room.id}, user.id))[agent_id]["waiting"]
        assert [(w["kind"], w["title"]) for w in waiting] == [("workspace_action", "새 방 만들기")]

    @pytest.mark.asyncio
    @pytest.mark.req("COL-20")
    async def test_inbox_approvals_and_questions_wait_on_their_requester(self, ws_env) -> None:
        sf, room, user = ws_env["session_factory"], ws_env["room"], ws_env["user"]
        agent_id, _pid = await _agent_in(sf, room.id)
        inbox = [
            {"id": "ap-1", "type": "approval", "needs_action": True, "task_title": "배포 승인",
             "task_room_id": room.id, "task_href": "/x", "approval": {"requester_agent_id": agent_id}},
            {"id": "q-1", "type": "question", "needs_action": True, "task_title": "질문",
             "task_room_id": room.id, "task_href": "/y", "question": {"requester_agent_id": agent_id}},
            {"id": "q-2", "type": "question", "needs_action": False, "task_title": "답한 질문",
             "task_room_id": room.id, "task_href": "/z", "question": {"requester_agent_id": agent_id}},
            {"id": "t-1", "type": "task", "needs_action": True, "task_title": "복구",
             "task_room_id": room.id, "task_href": "/w"},
        ]
        waiting = (await _alerts(sf, {room.id}, user.id, inbox))[agent_id]["waiting"]
        assert [(w["kind"], w["id"], w["href"]) for w in waiting] == [
            ("approval", "ap-1", "/x"), ("question", "q-1", "/y"),
        ]


class TestUnread:
    @pytest.mark.asyncio
    @pytest.mark.req("COL-20")
    async def test_finished_answer_is_unread_until_the_room_is_read(self, ws_env) -> None:
        sf, room, user = ws_env["session_factory"], ws_env["room"], ws_env["user"]
        agent_id, pid = await _agent_in(sf, room.id)
        message_id, seq = await _message(sf, room.id, pid)
        await _turn_row(sf, room.id, agent_id, "completed", minutes=1, message_id=message_id)
        skipped_id, _ = await _message(sf, room.id, pid)
        await _turn_row(sf, room.id, agent_id, "completed", minutes=2, message_id=skipped_id,
                        reason="agent_skipped")

        assert (await _alerts(sf, {room.id}, user.id))[agent_id]["unread_rooms"] == [room.id]

        async with sf() as db:
            (await db.get(Participant, ws_env["participant"].id)).last_read_message_seq = seq
            await db.commit()
        assert agent_id not in await _alerts(sf, {room.id}, user.id)


class TestVisibility:
    @pytest.mark.asyncio
    @pytest.mark.req("COL-20")
    async def test_rooms_outside_the_given_set_are_ignored(self, ws_env) -> None:
        sf, room, user = ws_env["session_factory"], ws_env["room"], ws_env["user"]
        async with sf() as db:
            other = Room(project_id=room.project_id, name="남의 방")
            db.add(other)
            await db.commit()
            other_id = other.id
        agent_id, pid = await _agent_in(sf, other_id)
        await _task(sf, other_id, pid, "failed")
        await _task(sf, other_id, pid, "todo")
        await _turn_row(sf, other_id, agent_id, "failed", minutes=1)
        async with sf() as db:
            db.add(WorkspaceActionRequest(
                agent_id=agent_id, agent_participant_id=pid, room_id=other_id, action="create_room",
                params={}, summary="몰래", status="pending", expires_at=NOW + timedelta(hours=1),
            ))
            await db.commit()

        assert await _alerts(sf, {room.id}, user.id) == {}
        assert await _alerts(sf, set(), user.id) == {}

    @pytest.mark.asyncio
    async def test_api_lists_alerts_for_people_only(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        agent_id, pid = await _agent_in(sf, room.id)
        await _task(sf, room.id, pid, "todo")
        await _task(sf, room.id, pid, "todo")
        agent_token, _s, _p = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            listed = client.get("/api/v1/agent-links/alerts", headers=_auth(ws_env["token"]))
            refused = client.get("/api/v1/agent-links/alerts", headers=_auth(agent_token))

        assert listed.status_code == 200
        by_agent = {a["agent_id"]: a for a in listed.json()["agents"]}
        assert by_agent[agent_id]["queued_tasks"] == 2
        assert by_agent[agent_id]["failed_tasks"] == []
        assert refused.status_code == 403
