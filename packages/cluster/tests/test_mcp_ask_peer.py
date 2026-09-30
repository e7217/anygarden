"""End-to-end tests for the ``ask_peer`` MCP tool (#737).

The tool checks a peer call during the caller's turn and schedules it;
the WS handler posts it as a thread reply under the caller's final reply,
through the normal agent-send path.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    Message,
    Participant,
)
from tests.test_peer_mention_safety_net import _seed_sender_and_peer as _seed_pair
from tests.test_peer_mention_safety_net import _add_token, _user_send


async def _seed_sender_and_peer(sf, room) -> tuple[str, str, str]:
    """Seed two agents that can hold durable turns (desired_state running)."""
    token, sender_pid, peer_pid = await _seed_pair(sf, room)
    async with sf() as db:
        for pid in (sender_pid, peer_pid):
            agent = await db.get(Agent, (await db.get(Participant, pid)).agent_id)
            agent.desired_state = "running"
        await db.commit()
    return token, sender_pid, peer_pid
from tests.test_ws_handler import ws_env as ws_env


def _ask_peer(client, token: str, room_id: str, target: str, question: str) -> dict:
    resp = client.post(
        "/mcp/rpc",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ask_peer",
                "arguments": {
                    "room_id": room_id,
                    "participant_id": target,
                    "question": question,
                },
            },
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


async def _turn_proof(sf, participant_id: str) -> dict:
    async with sf() as db:
        turn = (
            await db.scalars(
                select(AgentTurn).where(AgentTurn.target_participant_id == participant_id)
            )
        ).one()
        attempt = (
            await db.scalars(
                select(AgentTurnAttempt).where(
                    AgentTurnAttempt.turn_id == turn.request_id
                )
            )
        ).one()
        return {
            "request_id": turn.request_id,
            "turn_attempt": attempt.attempt_number,
            "turn_generation": attempt.generation,
            "turn_lease": attempt.lease_token,
        }


def _agent_reply(client, room_id: str, token: str, content: str, metadata: dict | None = None,
                 *, expect_messages: int = 1) -> list[dict]:
    """Send as the agent and collect the message frames it produced."""
    frames: list[dict] = []
    with client.websocket_connect(
        f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{token}"],
    ) as ws:
        ws.receive_text()  # welcome
        ws.send_text(json.dumps({"type": "send", "content": content, "metadata": metadata or {}}))
        for _ in range(20):
            frame = json.loads(ws.receive_text())
            assert frame.get("type") != "error", frame
            if frame.get("type") == "message":
                frames.append(frame)
                if len(frames) == expect_messages:
                    break
    return frames


class TestAskPeerTool:
    @pytest.mark.asyncio
    async def test_scheduled_ask_is_posted_in_a_thread_and_wakes_the_peer(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 배포 일정 알려 줘")
            result = _ask_peer(client, sender_token, room.id, peer_pid, "이번 주 배포 가능한 기능은?")
            assert result["isError"] is False
            assert result["structuredContent"]["status"] == "scheduled"

            proof = await _turn_proof(sf, sender_pid)
            reply, ask = _agent_reply(
                client, room.id, sender_token, "일정은 제가 정리하겠습니다.", proof,
                expect_messages=2,
            )

        assert reply["content"] == "일정은 제가 정리하겠습니다."
        assert ask["root_message_id"] == reply["id"]
        assert ask["content"] == f"<@user:{peer_pid}> 이번 주 배포 가능한 기능은?"
        assert ask["participant_id"] == sender_pid
        meta = ask.get("metadata") or {}
        assert meta.get("peer_ask") == {"via": "tool"}
        assert {"type": "user", "id": peer_pid} in meta.get("mentions", [])
        assert "peer_call_undelivered" not in meta

        async with sf() as db:
            peer_turn = (
                await db.scalars(
                    select(AgentTurn).where(AgentTurn.target_participant_id == peer_pid)
                )
            ).one()
            assert peer_turn.trigger_message_id == ask["id"]
            assert peer_turn.thread_root_id == reply["id"]

    @pytest.mark.asyncio
    async def test_already_woken_peer_is_rejected_with_messages_since_turn_start(
        self, ws_env
    ) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], "@everyone 각자 소개해 주세요")
            async with sf() as db:
                # The peer answered while the sender was still working.
                from anygarden.messages.service import append_message

                await append_message(db, room_id=room.id, participant_id=peer_pid,
                                     content="저는 peer입니다.")
                await db.commit()
            result = _ask_peer(client, sender_token, room.id, peer_pid, "소개해 주세요")
            proof = await _turn_proof(sf, sender_pid)
            frames = _agent_reply(client, room.id, sender_token, "저는 sender입니다.", proof)

        assert result["isError"] is False
        structured = result["structuredContent"]
        assert structured["status"] == "rejected"
        assert structured["reason"] == "already_answering"
        assert [m["content"] for m in structured["since_turn_start"]] == ["저는 peer입니다."]
        assert "저는 peer입니다." in result["content"][0]["text"]
        # Nothing is scheduled, so only the reply itself is posted.
        assert [f["content"] for f in frames] == ["저는 sender입니다."]
        async with sf() as db:
            assert (
                await db.scalars(select(Message).where(Message.root_message_id.isnot(None)))
            ).all() == []

    @pytest.mark.asyncio
    async def test_person_target_is_a_tool_error(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, _sender_pid, _peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            result = _ask_peer(client, sender_token, room.id, ws_env["participant"].id, "hi")

        assert result["isError"] is True
        assert "only calls agents" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_ask_to_a_peer_the_reply_already_called_is_not_delivered_twice(
        self, ws_env
    ) -> None:
        """A scheduled ask whose target the reply itself already called is
        marked undelivered instead of waking the peer a second time."""
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 도와줘")
            assert _ask_peer(client, sender_token, room.id, peer_pid, "확인 부탁")[
                "structuredContent"
            ]["status"] == "scheduled"
            proof = await _turn_proof(sf, sender_pid)
            _reply, ask = _agent_reply(
                client, room.id, sender_token, f"<@user:{peer_pid}> 먼저 이것부터", proof,
                expect_messages=2,
            )

        meta = ask.get("metadata") or {}
        assert meta.get("peer_redundant") is True
        assert meta.get("peer_call_undelivered") == [
            {"participant_id": peer_pid, "reason": "already_answering"}
        ]

    @pytest.mark.asyncio
    async def test_two_asks_in_one_turn_are_both_delivered(self, ws_env) -> None:
        """#756 — asking two peers from one turn is one hop, not two."""
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        other_pid = await _add_running_agent(sf, room, "agent01")

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 다들 뭐 할 수 있어?")
            for target in (other_pid, peer_pid):
                assert _ask_peer(client, sender_token, room.id, target, "무엇을 할 수 있나요?")[
                    "structuredContent"
                ]["status"] == "scheduled"
            proof = await _turn_proof(sf, sender_pid)
            _reply, *asks = _agent_reply(
                client, room.id, sender_token, "두 분께 물어봤습니다.", proof,
                expect_messages=3,
            )

        for ask in asks:
            meta = ask.get("metadata") or {}
            assert "peer_blocked" not in meta, meta
            assert "peer_call_undelivered" not in meta, meta
            assert meta.get("peer_depth") == 1
        async with sf() as db:
            woken = set(
                (
                    await db.scalars(
                        select(AgentTurn.target_participant_id).where(
                            AgentTurn.target_participant_id.in_([peer_pid, other_pid])
                        )
                    )
                ).all()
            )
        assert woken == {peer_pid, other_pid}

    @pytest.mark.asyncio
    async def test_ask_beyond_the_budget_is_rejected_by_the_tool(self, ws_env) -> None:
        """#756 — the tool's ``scheduled`` holds at send time; the ask that
        does not fit is rejected while the caller can still fix its reply."""
        from anygarden.orchestration.rules import PeerHandoffBudget

        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        other_pid = await _add_running_agent(sf, room, "agent01")
        app.state.peer_handoff_budget = PeerHandoffBudget(capacity=1)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 다들 뭐 할 수 있어?")
            first = _ask_peer(client, sender_token, room.id, other_pid, "q1")
            second = _ask_peer(client, sender_token, room.id, peer_pid, "q2")
            assert first["structuredContent"]["status"] == "scheduled"
            assert second["structuredContent"] == {
                **second["structuredContent"], "status": "rejected", "reason": "limit_reached",
            }
            proof = await _turn_proof(sf, sender_pid)
            _reply, ask = _agent_reply(
                client, room.id, sender_token, "agent01에게 물어봤습니다.", proof,
                expect_messages=2,
            )

        assert ask["content"].startswith(f"<@user:{other_pid}>")
        assert "peer_blocked" not in (ask.get("metadata") or {})

    @pytest.mark.asyncio
    async def test_peer_woken_by_an_ask_cannot_ask_on(self, ws_env) -> None:
        """A call from a turn another peer's call started is hop 2."""
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        other_pid = await _add_running_agent(sf, room, "agent01")
        peer_token = await _add_token(sf, peer_pid)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 도와줘")
            _ask_peer(client, sender_token, room.id, peer_pid, "확인 부탁")
            proof = await _turn_proof(sf, sender_pid)
            _agent_reply(client, room.id, sender_token, "물어봤습니다.", proof, expect_messages=2)

            result = _ask_peer(client, peer_token, room.id, other_pid, "너도 봐 줘")

        assert result["structuredContent"]["status"] == "rejected"
        assert result["structuredContent"]["reason"] == "limit_reached"

    @pytest.mark.asyncio
    async def test_new_human_message_drops_scheduled_asks(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 도와줘")
            _ask_peer(client, sender_token, room.id, peer_pid, "확인 부탁")
            assert app.state.pending_peer_asks.pending(
                (await _agent_id(sf, sender_pid)), room.id
            )
            _user_send(client, room.id, ws_env["token"], "다른 질문")

            assert app.state.pending_peer_asks.pending(
                (await _agent_id(sf, sender_pid)), room.id
            ) == []


async def _agent_id(sf, participant_id: str) -> str:
    async with sf() as db:
        return (await db.get(Participant, participant_id)).agent_id


async def _add_running_agent(sf, room, name: str) -> str:
    async with sf() as db:
        agent = Agent(
            name=name, engine="codex", actual_state="running", desired_state="running",
        )
        db.add(agent)
        await db.flush()
        part = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add(part)
        await db.commit()
        return part.id
