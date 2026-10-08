"""A human thread reply answers a general task's question over WS (#806)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from anygarden.db.models import AgentTurn, AgentTurnAttempt, Message, Task
from anygarden.db.task_input_request_models import TaskInputRequest
from tests.test_mcp_ask_peer import _open_turn, _seed_sender_and_peer
from tests.test_ws_handler import (
    ws_env as ws_env,  # noqa: PLC0414 — pytest fixture re-export
)


@pytest.fixture(autouse=True)
def _no_recovery_loop(monkeypatch) -> None:
    monkeypatch.setenv("ANYGARDEN_TURN_RECOVERY_INTERVAL_SEC", "0")


def _user_send(client, room_id: str, token: str, content: str,
               thread_root_id: str | None = None) -> dict:
    frame: dict = {"type": "send", "content": content}
    if thread_root_id is not None:
        frame["thread_root_id"] = thread_root_id
    with client.websocket_connect(
        f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{token}"],
    ) as ws:
        ws.receive_text()  # welcome
        ws.send_text(json.dumps(frame))
        for _ in range(10):
            out = json.loads(ws.receive_text())
            if out.get("type") == "message" and out.get("content") == content:
                return out
    raise AssertionError("echo never arrived")  # pragma: no cover


async def _lease_headers(sf, participant_id: str, agent_token: str) -> dict:
    turn = await _open_turn(sf, participant_id)
    async with sf() as db:
        turn = await db.get(AgentTurn, turn.request_id)
        attempt = await db.scalar(select(AgentTurnAttempt).where(
            AgentTurnAttempt.turn_id == turn.request_id,
        ))
        attempt.state = "leased"
        attempt.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        turn.state = "leased"
        await db.commit()
        return {
            "Authorization": f"Bearer {agent_token}",
            "X-Anygarden-Turn-Request-Id": turn.request_id,
            "X-Anygarden-Turn-Attempt": str(attempt.attempt_number),
            "X-Anygarden-Turn-Generation": str(attempt.generation),
            "X-Anygarden-Turn-Lease": attempt.lease_token,
        }


def _rpc(client, headers: dict, name: str, arguments: dict | None = None) -> dict:
    resp = client.post("/mcp/rpc", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


async def _ask_then_answer(ws_env, answer: str) -> dict:
    app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
    agent_token, bot_pid, _ = await _seed_sender_and_peer(sf, room)
    with TestClient(app) as client:
        trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{bot_pid}> 로그 조사해줘")
        headers = await _lease_headers(sf, bot_pid, agent_token)
        task_id = _rpc(client, headers, "claim_current_request")["task_id"]
        _rpc(client, headers, "request_task_input",
             {"question_key": "range", "question": "어느 기간인가요?"})
        reply = _user_send(
            client, room.id, ws_env["token"], answer.format(bot=bot_pid),
            thread_root_id=trigger["id"],
        )
    return {"sf": sf, "bot_pid": bot_pid, "task_id": task_id, "reply": reply,
            "trigger": trigger}


async def _turns_for(sf, participant_id: str) -> list[AgentTurn]:
    async with sf() as db:
        return list((await db.scalars(select(AgentTurn).where(
            AgentTurn.target_participant_id == participant_id,
        ).order_by(AgentTurn.created_at))).all())


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["지난 7일", "<@user:{bot}> 지난 7일"])
async def test_thread_reply_resumes_the_task_with_exactly_one_turn(ws_env, answer) -> None:
    out = await _ask_then_answer(ws_env, answer)
    sf = out["sf"]

    turns = await _turns_for(sf, out["bot_pid"])
    assert [t.trigger_message_id for t in turns].count(out["reply"]["id"]) == 0
    resumed = [t for t in turns if t.task_id == out["task_id"]
               and t.trigger_message_id != out["trigger"]["id"]]
    assert len(resumed) == 1
    async with sf() as db:
        request = await db.scalar(select(TaskInputRequest))
        task = await db.get(Task, out["task_id"])
        resume = await db.get(Message, resumed[0].trigger_message_id)
    assert request.status == "answered"
    assert request.answer_message_id == out["reply"]["id"]
    assert request.resume_message_id == resume.id
    assert task.status == "in_progress"
    assert "지난 7일" in task.spec


@pytest.mark.asyncio
async def test_thread_without_a_question_keeps_ordinary_behavior(ws_env) -> None:
    app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
    _, bot_pid, _ = await _seed_sender_and_peer(sf, room)
    with TestClient(app) as client:
        trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{bot_pid}> 안녕")
        reply = _user_send(client, room.id, ws_env["token"], f"<@user:{bot_pid}> 하나 더",
                           thread_root_id=trigger["id"])

    turns = await _turns_for(sf, bot_pid)
    assert [t.trigger_message_id for t in turns] == [trigger["id"], reply["id"]]
    async with sf() as db:
        assert (await db.scalars(select(Task))).first() is None
