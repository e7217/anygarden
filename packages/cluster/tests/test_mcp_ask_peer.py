"""End-to-end tests for the ``ask_peer`` MCP tool and its async fan-in (#762).

``ask_peer`` posts questions at once in the caller's thread and starts the
peers' turns. The caller's reply for that turn becomes a draft instead of a
message, and once every peer turn is terminal the turn-recovery worker
wakes the caller with a hidden result message in its original conversation.

The background recovery loop is disabled so each test drives
``dispatch_peer_ask_groups`` itself.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    Message,
    Participant,
    PeerAskGroup,
    PeerAskTarget,
)
from anygarden.orchestration.peer_fanin import dispatch_peer_ask_groups
from tests.test_peer_mention_safety_net import _add_token
from tests.test_peer_mention_safety_net import _seed_sender_and_peer as _seed_pair
from tests.test_ws_handler import ws_env as ws_env


@pytest.fixture(autouse=True)
def _no_recovery_loop(monkeypatch) -> None:
    monkeypatch.setenv("ANYGARDEN_TURN_RECOVERY_INTERVAL_SEC", "0")


async def _seed_sender_and_peer(sf, room) -> tuple[str, str, str]:
    """Seed two agents that can hold durable turns (desired_state running)."""
    token, sender_pid, peer_pid = await _seed_pair(sf, room)
    async with sf() as db:
        for pid in (sender_pid, peer_pid):
            agent = await db.get(Agent, (await db.get(Participant, pid)).agent_id)
            agent.desired_state = "running"
        await db.commit()
    return token, sender_pid, peer_pid


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


def _user_send(client, room_id: str, token: str, content: str) -> dict:
    with client.websocket_connect(
        f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{token}"],
    ) as ws:
        ws.receive_text()  # welcome
        ws.send_text(json.dumps({"type": "send", "content": content}))
        for _ in range(10):
            frame = json.loads(ws.receive_text())
            if frame.get("type") == "message":
                return frame
    raise AssertionError("echo never arrived")  # pragma: no cover


def _ask_peer(client, token: str, room_id: str, asks: list[tuple[str, str]]) -> dict:
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
                    "asks": [{"participant_id": p, "question": q} for p, q in asks],
                },
            },
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


async def _open_turn(sf, participant_id: str) -> AgentTurn:
    async with sf() as db:
        return (
            await db.scalars(
                select(AgentTurn)
                .where(
                    AgentTurn.target_participant_id == participant_id,
                    AgentTurn.state.in_(("pending", "leased", "retrying", "completing")),
                )
                .order_by(AgentTurn.created_at.desc())
            )
        ).first()


async def _proof(sf, participant_id: str) -> dict:
    turn = await _open_turn(sf, participant_id)
    assert turn is not None, "no open turn"
    async with sf() as db:
        attempt = (
            await db.scalars(
                select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == turn.request_id)
            )
        ).one()
    return {
        "request_id": turn.request_id,
        "turn_attempt": attempt.attempt_number,
        "turn_generation": attempt.generation,
        "turn_lease": attempt.lease_token,
    }


def _agent_send(client, room_id: str, token: str, content: str, metadata: dict,
                thread_root_id: str | None = None) -> None:
    """Send as the agent; wait until the server has processed the frame."""
    frame: dict = {"type": "send", "content": content, "metadata": metadata}
    if thread_root_id is not None:
        frame["thread_root_id"] = thread_root_id
    with client.websocket_connect(
        f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{token}"],
    ) as ws:
        ws.receive_text()  # welcome
        ws.send_text(json.dumps(frame))
        # Frames are handled in order, so the echo of a typing frame sent
        # next proves the send above was processed.
        ws.send_text(json.dumps({"type": "typing", "is_typing": False}))
        for _ in range(20):
            out = json.loads(ws.receive_text())
            assert out.get("type") != "error", out
            if out.get("type") == "typing" and out.get("is_typing") is False:
                return
    raise AssertionError("typing echo never arrived")  # pragma: no cover


async def _wait_turn_state(sf, request_id: str, state: str) -> None:
    for _ in range(50):
        async with sf() as db:
            turn = await db.get(AgentTurn, request_id)
            if turn is not None and turn.state == state:
                return
        time.sleep(0.02)
    raise AssertionError(f"turn {request_id} never reached {state}")  # pragma: no cover


async def _group(sf) -> PeerAskGroup:
    async with sf() as db:
        return (await db.scalars(select(PeerAskGroup))).one()


async def _targets(sf) -> dict[str, PeerAskTarget]:
    async with sf() as db:
        rows = (await db.scalars(select(PeerAskTarget))).all()
    return {r.target_participant_id: r for r in rows}


class TestAskPeerPostsAtOnce:
    @pytest.mark.asyncio
    async def test_questions_are_posted_in_the_trigger_thread_before_the_reply(
        self, ws_env
    ) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        other_pid = await _add_running_agent(sf, room, "agent01")

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 리소스 점검")
            result = _ask_peer(client, sender_token, room.id, [
                (peer_pid, "디스크 확인해 줘"), (other_pid, "메모리 확인해 줘"),
            ])

        assert result["isError"] is False
        assert result["structuredContent"]["status"] == "sent"
        assert "will NOT be posted" in result["content"][0]["text"]
        targets = await _targets(sf)
        assert set(targets) == {peer_pid, other_pid}
        async with sf() as db:
            for pid, row in targets.items():
                question = await db.get(Message, row.question_message_id)
                assert question.root_message_id == trigger["id"]
                assert question.participant_id == sender_pid
                assert question.content.startswith(f"<@user:{pid}> ")
                assert question.extra_metadata["peer_ask"]["via"] == "tool"
                turn = await db.get(AgentTurn, row.request_id)
                assert turn.target_participant_id == pid
                assert turn.trigger_message_id == question.id
                assert turn.thread_root_id == trigger["id"]
                assert turn.state == "pending"
        group = await _group(sf)
        assert group.scope_thread_root_id is None
        assert group.state == "collecting"

    @pytest.mark.asyncio
    async def test_single_participant_arguments_still_work(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 질문")
            resp = client.post(
                "/mcp/rpc",
                headers={"Authorization": f"Bearer {sender_token}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "ask_peer",
                    "arguments": {"room_id": room.id, "participant_id": peer_pid, "question": "q"},
                }},
            )

        assert resp.json()["result"]["structuredContent"]["status"] == "sent"

    @pytest.mark.asyncio
    async def test_peer_with_an_open_turn_is_rejected_with_recent_messages(
        self, ws_env
    ) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, _sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            # @everyone wakes both agents; the peer is answering the same message.
            _user_send(client, room.id, ws_env["token"], "@everyone 각자 소개해 주세요")
            async with sf() as db:
                from anygarden.messages.service import append_message

                await append_message(db, room_id=room.id, participant_id=peer_pid,
                                     content="저는 peer입니다.")
                await db.commit()
            result = _ask_peer(client, sender_token, room.id, [(peer_pid, "소개해 주세요")])

        structured = result["structuredContent"]
        assert result["isError"] is False
        assert structured["status"] == "rejected"
        assert structured["targets"][0]["reason"] == "already_answering"
        assert [m["content"] for m in structured["since_turn_start"]] == ["저는 peer입니다."]
        async with sf() as db:
            assert (await db.scalars(select(PeerAskGroup))).all() == []

    @pytest.mark.asyncio
    async def test_person_target_is_a_tool_error(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, _peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 질문")
            result = _ask_peer(client, sender_token, room.id, [(ws_env["participant"].id, "hi")])

        assert result["isError"] is True
        assert "only calls agents" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_no_open_turn_is_a_tool_error(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, _sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            result = _ask_peer(client, sender_token, room.id, [(peer_pid, "hi")])

        assert result["isError"] is True
        assert "while you are answering" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_ask_beyond_the_budget_is_rejected(self, ws_env) -> None:
        from anygarden.orchestration.rules import PeerHandoffBudget

        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        other_pid = await _add_running_agent(sf, room, "agent01")
        app.state.peer_handoff_budget = PeerHandoffBudget(capacity=1)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 다들?")
            app.state.peer_handoff_budget = PeerHandoffBudget(capacity=1)
            result = _ask_peer(client, sender_token, room.id, [(other_pid, "q1"), (peer_pid, "q2")])

        by_pid = {t["participant_id"]: t for t in result["structuredContent"]["targets"]}
        assert by_pid[other_pid]["status"] == "accepted"
        assert by_pid[peer_pid] == {**by_pid[peer_pid], "status": "rejected", "reason": "limit_reached"}
        assert set(await _targets(sf)) == {other_pid}


class TestFanIn:
    @pytest.mark.asyncio
    async def test_caller_reply_becomes_a_draft_and_the_caller_is_woken_with_answers(
        self, ws_env
    ) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        other_pid = await _add_running_agent(sf, room, "agent01")
        peer_token = await _add_token(sf, peer_pid)
        other_token = await _add_token(sf, other_pid)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 리소스 점검")
            _ask_peer(client, sender_token, room.id, [
                (peer_pid, "디스크 확인해 줘"), (other_pid, "메모리 확인해 줘"),
            ])
            caller_proof = await _proof(sf, sender_pid)
            _agent_send(client, room.id, sender_token, "CPU는 12%입니다.", caller_proof)
            await _wait_turn_state(sf, caller_proof["request_id"], "completed")

            # The draft is not a message.
            async with sf() as db:
                posted = (await db.scalars(
                    select(Message.content).where(Message.participant_id == sender_pid)
                )).all()
            assert "CPU는 12%입니다." not in posted
            group = await _group(sf)
            assert group.caller_draft == "CPU는 12%입니다."
            assert group.caller_closed_at is not None
            assert group.state == "collecting"

            _agent_send(client, room.id, peer_token, "디스크 40% 사용",
                        await _proof(sf, peer_pid), thread_root_id=trigger["id"])
            assert (await _group(sf)).state == "collecting"
            _agent_send(client, room.id, other_token, "bwrap 제한으로 점검 실패",
                        await _proof(sf, other_pid), thread_root_id=trigger["id"])
            assert (await _group(sf)).state == "ready"

            assert await dispatch_peer_ask_groups(sf, app.state.connection_manager) == 1

        group = await _group(sf)
        assert group.state == "woken"
        async with sf() as db:
            wake_turn = await db.get(AgentTurn, group.wake_request_id)
            wake = await db.get(Message, wake_turn.trigger_message_id)
            caller_turn = await db.get(AgentTurn, caller_proof["request_id"])
        assert caller_turn.terminal_reason == "awaiting_peers"
        assert wake_turn.target_participant_id == sender_pid
        assert wake_turn.state == "pending"
        # Same conversation as the caller's first turn: the main channel.
        assert wake.root_message_id is None
        assert wake_turn.thread_root_id is None
        assert wake.participant_id is None
        assert wake.extra_metadata["system_origin"] == "peer_ask_results"
        assert wake.content.startswith(f"<@user:{sender_pid}> [ask_peer results]")
        for needle in ("리소스 점검", "CPU는 12%입니다.", "디스크 40% 사용",
                       "bwrap 제한으로 점검 실패", "디스크 확인해 줘"):
            assert needle in wake.content

    @pytest.mark.asyncio
    async def test_deadline_times_out_silent_peers_and_still_wakes(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "오래 걸리는 일")])
            _agent_send(client, room.id, sender_token, "기다립니다.", await _proof(sf, sender_pid))
            assert await dispatch_peer_ask_groups(sf, None) == 0
            async with sf() as db:
                group = (await db.scalars(select(PeerAskGroup))).one()
                group.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await db.commit()
            assert await dispatch_peer_ask_groups(sf, None) == 1

        row = (await _targets(sf))[peer_pid]
        assert row.state == "timeout"
        async with sf() as db:
            group = (await db.scalars(select(PeerAskGroup))).one()
            wake = await db.get(Message, (await db.get(AgentTurn, group.wake_request_id)).trigger_message_id)
        assert "timeout (deadline)" in wake.content

    @pytest.mark.asyncio
    async def test_peer_that_cannot_run_settles_at_once(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        async with sf() as db:
            agent = await db.get(Agent, (await db.get(Participant, peer_pid)).agent_id)
            agent.desired_state = "stopped"
            await db.commit()

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "q")])
            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid))

        row = (await _targets(sf))[peer_pid]
        assert (row.state, row.reason) == ("cancelled", "agent_not_running")
        assert (await _group(sf)).state == "ready"

    @pytest.mark.asyncio
    async def test_hop2_ask_is_forwarded_to_the_caller(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        other_pid = await _add_running_agent(sf, room, "agent01")
        peer_token = await _add_token(sf, peer_pid)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "디스크 확인")])
            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid))

            result = _ask_peer(client, peer_token, room.id, [(other_pid, "너도 봐 줘")])
            assert result["structuredContent"]["status"] == "forwarded"
            _agent_send(client, room.id, peer_token, "디스크 OK",
                        await _proof(sf, peer_pid), thread_root_id=trigger["id"])
            assert await dispatch_peer_ask_groups(sf, None) == 1

        assert (await _targets(sf))[peer_pid].forwarded_requests == [
            {"participant_id": other_pid, "question": "너도 봐 줘"}
        ]
        assert await _open_turn(sf, other_pid) is None
        async with sf() as db:
            group = (await db.scalars(select(PeerAskGroup))).one()
            wake = await db.get(Message, (await db.get(AgentTurn, group.wake_request_id)).trigger_message_id)
        assert "It asked you to ask agent01: 너도 봐 줘" in wake.content

    @pytest.mark.asyncio
    async def test_woken_caller_can_ask_a_finished_peer_again(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        peer_token = await _add_token(sf, peer_pid)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "1차 질문")])
            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid))
            _agent_send(client, room.id, peer_token, "1차 답",
                        await _proof(sf, peer_pid), thread_root_id=trigger["id"])
            assert await dispatch_peer_ask_groups(sf, None) == 1

            # The woken turn is hop 1 (its trigger has no agent author).
            result = _ask_peer(client, sender_token, room.id, [(peer_pid, "2차 질문")])

        assert result["structuredContent"]["status"] == "sent"
        async with sf() as db:
            groups = (await db.scalars(select(PeerAskGroup))).all()
        assert len(groups) == 2

    @pytest.mark.asyncio
    async def test_a_group_is_woken_only_once(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        peer_token = await _add_token(sf, peer_pid)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "q")])
            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid))
            _agent_send(client, room.id, peer_token, "답",
                        await _proof(sf, peer_pid), thread_root_id=trigger["id"])
            assert await dispatch_peer_ask_groups(sf, None) == 1
            assert await dispatch_peer_ask_groups(sf, None) == 0

        async with sf() as db:
            wakes = (await db.scalars(select(AgentTurn).where(
                AgentTurn.target_participant_id == sender_pid,
            ))).all()
        assert len(wakes) == 2  # the original turn and one wake

    @pytest.mark.asyncio
    async def test_caller_in_a_thread_is_woken_in_that_thread(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        peer_token = await _add_token(sf, peer_pid)

        with TestClient(app) as client:
            root = _user_send(client, room.id, ws_env["token"], "스레드 시작")
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{ws_env['token']}"],
            ) as ws:
                ws.receive_text()
                ws.send_text(json.dumps({
                    "type": "send", "content": f"<@user:{sender_pid}> 여기서 점검",
                    "thread_root_id": root["id"],
                }))
                ws.receive_text()
            _ask_peer(client, sender_token, room.id, [(peer_pid, "q")])
            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid),
                        thread_root_id=root["id"])
            _agent_send(client, room.id, peer_token, "답",
                        await _proof(sf, peer_pid), thread_root_id=root["id"])
            assert await dispatch_peer_ask_groups(sf, None) == 1

        group = await _group(sf)
        assert group.scope_thread_root_id == root["id"]
        async with sf() as db:
            wake_turn = await db.get(AgentTurn, group.wake_request_id)
        assert wake_turn.thread_root_id == root["id"]


class _FakeManager:
    def __init__(self) -> None:
        self.frames: list = []

    async def broadcast(self, room_id, frame) -> None:
        self.frames.append(frame)

    async def broadcast_tailored(self, room_id, make_frame) -> set[str]:
        self.frames.append(make_frame("someone-else"))
        return set()


class TestWaitingIndicator:
    @pytest.mark.asyncio
    async def test_caller_shows_waiting_for_peers_until_woken(self, ws_env) -> None:
        from anygarden.orchestration import peer_fanin

        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        peer_token = await _add_token(sf, peer_pid)
        peer_fanin._last_heartbeat.clear()
        manager = _FakeManager()

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "q")])
            # Before the caller's turn closes it is still typing on its own.
            await dispatch_peer_ask_groups(sf, manager)
            assert manager.frames == []

            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid))
            await dispatch_peer_ask_groups(sf, manager)
            (frame,) = manager.frames
            assert frame.participant_id == sender_pid
            assert frame.is_typing is True
            assert frame.stage == "waiting_peers"
            assert (frame.waiting_done, frame.waiting_total) == (0, 1)
            assert frame.waiting_names == ["peer"]

            # Rate-limited: an immediate second pass sends nothing new.
            await dispatch_peer_ask_groups(sf, manager)
            assert len(manager.frames) == 1

            _agent_send(client, room.id, peer_token, "답",
                        await _proof(sf, peer_pid), thread_root_id=trigger["id"])
            await dispatch_peer_ask_groups(sf, manager)

        stop, wake = manager.frames[1:]
        assert (stop.participant_id, stop.is_typing) == (sender_pid, False)
        assert wake.metadata["system_origin"] == "peer_ask_results"


class TestTargetStates:
    @pytest.mark.asyncio
    async def test_a_timed_out_target_stays_timed_out_when_the_peer_answers_late(
        self, ws_env
    ) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        peer_token = await _add_token(sf, peer_pid)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "q")])
            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid))
            async with sf() as db:
                group = (await db.scalars(select(PeerAskGroup))).one()
                group.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await db.commit()
            peer_proof = await _proof(sf, peer_pid)
            assert await dispatch_peer_ask_groups(sf, None) == 1
            _agent_send(client, room.id, peer_token, "늦은 답", peer_proof,
                        thread_root_id=trigger["id"])

        row = (await _targets(sf))[peer_pid]
        assert (row.state, row.reason) == ("timeout", "deadline")


def test_render_results_lists_every_peer_with_its_outcome() -> None:
    from anygarden.orchestration.peer_fanin import render_results

    text = render_results(
        caller_pid="pm",
        origin="호스트 리소스 점검",
        draft="CPU 12%",
        entries=[
            {"name": "local-agent", "state": "completed", "reason": None,
             "question": "디스크?", "answer": "40%", "forwarded": []},
            {"name": "agent01", "state": "failed", "reason": "retry_exhausted",
             "question": "메모리?", "answer": None,
             "forwarded": [{"name": "PM2", "question": "네트워크?"}]},
            {"name": "agent02", "state": "completed", "reason": "declined",
             "question": "로그?", "answer": None, "forwarded": []},
        ],
    )

    assert text.startswith("<@user:pm> [ask_peer results]")
    assert "Original request:\n호스트 리소스 점검" in text
    assert "Your draft from before you asked (not posted):\nCPU 12%" in text
    assert "1. local-agent — completed\nQuestion: 디스크?\nAnswer:\n40%" in text
    assert "2. agent01 — failed (retry_exhausted)" in text
    assert "It asked you to ask PM2: 네트워크?" in text
    assert "3. agent02 — completed (declined)\nQuestion: 로그?\nAnswer: (no reply was posted)" in text


class TestReviewFixes:
    @pytest.mark.asyncio
    async def test_second_round_questions_hang_off_the_original_message(self, ws_env) -> None:
        """After a wake the trigger is the hidden result message, which the
        UI never renders; round-2 questions must stay in a visible thread."""
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        peer_token = await _add_token(sf, peer_pid)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "1차")])
            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid))
            _agent_send(client, room.id, peer_token, "1차 답",
                        await _proof(sf, peer_pid), thread_root_id=trigger["id"])
            assert await dispatch_peer_ask_groups(sf, None) == 1
            _ask_peer(client, sender_token, room.id, [(peer_pid, "2차")])

        async with sf() as db:
            second = (await db.scalars(
                select(PeerAskTarget).where(PeerAskTarget.question == "2차")
            )).one()
            question = await db.get(Message, second.question_message_id)
            group = await db.get(PeerAskGroup, second.group_id)
            wake = await db.get(Message, group.trigger_message_id)
        assert wake.extra_metadata["system_origin"] == "peer_ask_results"
        assert question.root_message_id == trigger["id"]

    @pytest.mark.asyncio
    async def test_delegation_result_is_not_absorbed(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "q")])
            proof = await _proof(sf, sender_pid)
            _agent_send(client, room.id, sender_token, "위임 결과", {
                **proof, "delegation_id": "d-1", "delegation_outcome": "ok",
            })

        group = await _group(sf)
        assert group.caller_draft is None
        async with sf() as db:
            posted = (await db.scalars(
                select(Message.content).where(Message.participant_id == sender_pid)
            )).all()
        assert "위임 결과" in posted

    @pytest.mark.asyncio
    async def test_a_missed_ready_transition_is_repaired_by_the_worker(self, ws_env) -> None:
        """A peer and the caller closing at once can each miss the other's
        write; the worker re-checks every group whose caller is closed."""
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        peer_token = await _add_token(sf, peer_pid)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "q")])
            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid))
            _agent_send(client, room.id, peer_token, "답",
                        await _proof(sf, peer_pid), thread_root_id=trigger["id"])
            async with sf() as db:
                group = (await db.scalars(select(PeerAskGroup))).one()
                group.state = "collecting"
                await db.commit()
            assert await dispatch_peer_ask_groups(sf, None) == 1

        assert (await _group(sf)).state == "woken"

    @pytest.mark.asyncio
    async def test_racing_group_creation_joins_the_winner(self, ws_env, monkeypatch) -> None:
        from anygarden.orchestration import peer_fanin

        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "q")])
        winner = await _group(sf)

        real = peer_fanin.group_for_caller
        calls = {"n": 0}

        async def blind_first(db, request_id):
            calls["n"] += 1
            return None if calls["n"] == 1 else await real(db, request_id)

        monkeypatch.setattr(peer_fanin, "group_for_caller", blind_first)
        async with sf() as db:
            turn = await db.get(AgentTurn, winner.caller_request_id)
            joined = await peer_fanin.open_or_join_group(
                db, caller_turn=turn, caller_participant_id=sender_pid
            )
            assert joined.id == winner.id

    @pytest.mark.asyncio
    async def test_one_failing_group_does_not_block_the_others(self, ws_env, monkeypatch) -> None:
        from anygarden.orchestration import peer_fanin

        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        peer_token = await _add_token(sf, peer_pid)

        with TestClient(app) as client:
            trigger = _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 점검")
            _ask_peer(client, sender_token, room.id, [(peer_pid, "q")])
            _agent_send(client, room.id, sender_token, "초안", await _proof(sf, sender_pid))
            _agent_send(client, room.id, peer_token, "답",
                        await _proof(sf, peer_pid), thread_root_id=trigger["id"])
        good = await _group(sf)
        async with sf() as db:
            bad = PeerAskGroup(
                id="bad-group", room_id=room.id, caller_participant_id=sender_pid,
                caller_request_id="bad-turn", state="ready",
                deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            )
            db.add(bad)
            await db.commit()

        real_wake = peer_fanin._wake

        async def failing(db, group):
            if group.id == "bad-group":
                raise RuntimeError("boom")
            return await real_wake(db, group)

        monkeypatch.setattr(peer_fanin, "_wake", failing)
        assert await dispatch_peer_ask_groups(sf, None) == 1
        async with sf() as db:
            assert (await db.get(PeerAskGroup, good.id)).state == "woken"
            # The failed claim rolled back and is retried on the next tick.
            assert (await db.get(PeerAskGroup, "bad-group")).state == "ready"

