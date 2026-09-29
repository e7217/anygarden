# ruff: noqa: F811
"""Integration tests for the peer-mention safety net (#279 / #644).

Covers the WS-handler wiring of ``peer_depth``, ``kind``, and the
``PeerHandoffBudget`` cap. The orchestration helpers themselves are
unit-tested in ``test_orchestration.py``; this file exercises the
end-to-end stamping and strip behaviour through the real handler.

#644 removed ``agents.collaboration_mode``. These tests are the
evidence that the removal was safe to make: the cap never consulted
that column, so it behaves identically without it. A "solo" agent was
never prevented from waking a peer — only the budget below ever
stopped one.

The ``F811`` ignore covers the pytest-fixture import idiom — pytest
recognises a fixture either by import or by parameter name, and ruff
flags the latter as a redefinition false-positive.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.db.models import (
    Agent,
    AgentToken,
    Participant,
)

# ws_env fixture is defined in test_ws_handler.py — pytest picks it up
# transitively when the symbol is imported here. F401 (unused import) and
# F811 (redefinition) are pytest-fixture false positives.
from tests.test_ws_handler import ws_env as ws_env  # noqa: F401


class TestPeerMentionStamping:
    """Issue #279 §3 — broadcast metadata must carry ``peer_depth``
    and ``kind`` whenever an agent message contains a mention pointing
    at another agent participant."""

    @pytest.mark.asyncio
    async def test_agent_peer_mention_first_layer_stamped(
        self, ws_env
    ) -> None:
        """First peer-ask of a turn → ``peer_depth=1``, ``kind="peer_query"``,
        no ``peer_blocked`` flag."""
        app = ws_env["app"]
        sf = ws_env["session_factory"]
        room = ws_env["room"]

        # Seed two agents in the room so the sender has a real peer to
        # mention. Capture the peer participant_id for the mention token.
        async with sf() as db:
            sender = Agent(
                name="sender",
                engine="codex",
                actual_state="running",
            )
            peer = Agent(name="peer", engine="codex", actual_state="running")
            db.add_all([sender, peer])
            await db.flush()

            sender_part = Participant(
                room_id=room.id, agent_id=sender.id, role="member"
            )
            peer_part = Participant(
                room_id=room.id, agent_id=peer.id, role="member"
            )
            db.add_all([sender_part, peer_part])
            await db.flush()

            sender_token_plain = generate_token()
            token_hash, lookup_hint = hash_agent_token(sender_token_plain)
            db.add(AgentToken(
                agent_id=sender.id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            ))
            await db.commit()
            await db.refresh(peer_part)
            peer_pid = peer_part.id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{sender_token_plain}"],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"의견 좀 <@user:{peer_pid}>",
                }))
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "message"
                meta = msg.get("metadata") or {}
                assert meta.get("peer_depth") == 1
                assert meta.get("kind") == "peer_query"
                assert meta.get("peer_blocked") is None
                # Mention token survives in the broadcast content
                # because depth-1 is below the cap.
                assert f"<@user:{peer_pid}>" in msg["content"]

    @pytest.mark.asyncio
    async def test_agent_second_peer_mention_in_same_turn_stripped(
        self, ws_env
    ) -> None:
        """Second peer-ask in the same user turn exceeds
        ``MAX_PEER_DEPTH=1`` → mention is stripped, ``peer_blocked``
        flag is set, content remains readable."""
        app = ws_env["app"]
        sf = ws_env["session_factory"]
        room = ws_env["room"]

        async with sf() as db:
            sender = Agent(
                name="sender",
                engine="codex",
                actual_state="running",
            )
            peer = Agent(name="peer", engine="codex", actual_state="running")
            db.add_all([sender, peer])
            await db.flush()

            sender_part = Participant(
                room_id=room.id, agent_id=sender.id, role="member"
            )
            peer_part = Participant(
                room_id=room.id, agent_id=peer.id, role="member"
            )
            db.add_all([sender_part, peer_part])
            await db.flush()

            sender_token_plain = generate_token()
            token_hash, lookup_hint = hash_agent_token(sender_token_plain)
            db.add(AgentToken(
                agent_id=sender.id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            ))
            await db.commit()
            await db.refresh(peer_part)
            peer_pid = peer_part.id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{sender_token_plain}"],
            ) as ws:
                ws.receive_text()  # welcome
                # First peer-ask passes (depth=1).
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"<@user:{peer_pid}> 1차 질문",
                }))
                first = json.loads(ws.receive_text())
                assert first["metadata"]["peer_depth"] == 1
                # Second peer-ask without a human turn-break in between
                # exceeds MAX_PEER_DEPTH and gets the mention stripped.
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"<@user:{peer_pid}> 2차 질문",
                }))
                second = json.loads(ws.receive_text())
                meta = second.get("metadata") or {}
                assert meta.get("peer_blocked") is True
                # Content still flows through (so the user sees the
                # response) but the peer-mention token is gone.
                assert f"<@user:{peer_pid}>" not in second["content"]
                assert "2차 질문" in second["content"]

    @pytest.mark.asyncio
    async def test_user_send_resets_peer_budget(self, ws_env) -> None:
        """A human/guest send opens a fresh user turn → the budget
        reset lets the next agent peer-ask pass at depth=1 again."""
        app = ws_env["app"]
        token = ws_env["token"]
        sf = ws_env["session_factory"]
        room = ws_env["room"]

        async with sf() as db:
            sender = Agent(
                name="sender",
                engine="codex",
                actual_state="running",
            )
            peer = Agent(name="peer", engine="codex", actual_state="running")
            db.add_all([sender, peer])
            await db.flush()
            sender_part = Participant(
                room_id=room.id, agent_id=sender.id, role="member"
            )
            peer_part = Participant(
                room_id=room.id, agent_id=peer.id, role="member"
            )
            db.add_all([sender_part, peer_part])
            await db.flush()

            sender_token_plain = generate_token()
            token_hash, lookup_hint = hash_agent_token(sender_token_plain)
            db.add(AgentToken(
                agent_id=sender.id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            ))
            await db.commit()
            await db.refresh(peer_part)
            peer_pid = peer_part.id
            sender_pid = sender_part.id

        with TestClient(app) as client:
            # Connect as the human user first to drive the budget
            # reset; then connect as the agent in a separate session
            # to send the peer-ask. Two clients in two contexts is
            # the closest analogue to the production flow without
            # juggling two sockets in one TestClient.
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as user_ws:
                user_ws.receive_text()  # welcome
                # First user message (resets budget to capacity). It
                # addresses only the sender so the peer is not already
                # woken by this turn (#719 would drop that mention).
                user_ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"<@user:{sender_pid}> Hi",
                }))
                user_ws.receive_text()  # echo

            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{sender_token_plain}"],
            ) as agent_ws:
                agent_ws.receive_text()  # welcome
                # Drain any pre-existing replay messages until a new
                # message we send shows up. Simpler to send first then
                # collect the matching echo.
                agent_ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"<@user:{peer_pid}> peer ask",
                }))
                # The agent's own send echoes back as its reply; older
                # messages on the room are also replayed since this is
                # a fresh subscription. Skim until we see ours.
                for _ in range(10):
                    raw = agent_ws.receive_text()
                    msg = json.loads(raw)
                    if msg.get("type") == "message" and "peer ask" in msg.get(
                        "content", ""
                    ):
                        break
                else:  # pragma: no cover — guard a flaky test, not real prod
                    pytest.fail("agent send was never echoed back")
                meta = msg.get("metadata") or {}
                # First post-reset peer-ask passes at depth=1.
                assert meta.get("peer_depth") == 1
                assert meta.get("kind") == "peer_query"
                assert meta.get("peer_blocked") is None


async def _seed_sender_and_peer(sf, room) -> tuple[str, str, str]:
    """Seed two running agents in *room*; return (sender_token, sender_pid, peer_pid)."""
    async with sf() as db:
        sender = Agent(name="sender", engine="codex", actual_state="running")
        peer = Agent(name="peer", engine="codex", actual_state="running")
        db.add_all([sender, peer])
        await db.flush()
        sender_part = Participant(room_id=room.id, agent_id=sender.id, role="member")
        peer_part = Participant(room_id=room.id, agent_id=peer.id, role="member")
        db.add_all([sender_part, peer_part])
        await db.flush()
        sender_token_plain = generate_token()
        token_hash, lookup_hint = hash_agent_token(sender_token_plain)
        db.add(AgentToken(
            agent_id=sender.id, token_hash=token_hash, lookup_hint=lookup_hint,
        ))
        await db.commit()
        return sender_token_plain, sender_part.id, peer_part.id


def _user_send(client, room_id: str, token: str, content: str) -> None:
    with client.websocket_connect(
        f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{token}"],
    ) as user_ws:
        user_ws.receive_text()  # welcome
        user_ws.send_text(json.dumps({"type": "send", "content": content}))
        user_ws.receive_text()  # echo


def _agent_send_and_capture(client, room_id: str, token: str, content: str, marker: str) -> dict:
    with client.websocket_connect(
        f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{token}"],
    ) as agent_ws:
        agent_ws.receive_text()  # welcome
        agent_ws.send_text(json.dumps({"type": "send", "content": content}))
        for _ in range(10):
            msg = json.loads(agent_ws.receive_text())
            if msg.get("type") == "message" and marker in msg.get("content", ""):
                return msg
    pytest.fail("agent send was never echoed back")  # pragma: no cover


class TestRedundantPeerWake:
    """#719 — a peer mention must not re-wake an agent that the same
    user turn already woke; that agent is answering the same question."""

    @pytest.mark.asyncio
    async def test_peer_mention_to_already_woken_agent_is_stripped(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, _sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            # @everyone in a mentioned_only room → every agent is woken (#739).
            _user_send(client, room.id, ws_env["token"], "@everyone 각자 무엇을 할 수 있나")
            msg = _agent_send_and_capture(
                client, room.id, sender_token,
                f"저는 코드를 봅니다. <@user:{peer_pid}> 소개해 주세요", "코드를 봅니다",
            )

        meta = msg.get("metadata") or {}
        assert f"<@user:{peer_pid}>" not in msg["content"]
        assert "소개해 주세요" in msg["content"]
        assert meta.get("peer_redundant") is True
        assert "mentions" not in meta
        assert meta.get("kind") is None
        budget = app.state.peer_handoff_budget
        assert budget.remaining(room.id) == budget._capacity

    @pytest.mark.asyncio
    async def test_peer_mention_to_unwoken_agent_still_allowed(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            # The human addresses only the sender; the peer stays asleep.
            _user_send(client, room.id, ws_env["token"], f"<@user:{sender_pid}> 도와줘")
            msg = _agent_send_and_capture(
                client, room.id, sender_token, f"<@user:{peer_pid}> 의견 부탁", "의견 부탁",
            )

        meta = msg.get("metadata") or {}
        assert f"<@user:{peer_pid}>" in msg["content"]
        assert meta.get("peer_depth") == 1
        assert meta.get("kind") == "peer_query"
        assert meta.get("peer_redundant") is None

    @pytest.mark.asyncio
    async def test_non_mentioned_only_room_does_not_mark_woken(self, ws_env) -> None:
        from anygarden.db.models import Room

        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, _sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)
        async with sf() as db:
            (await db.get(Room, room.id)).speaker_strategy = "round_robin"
            await db.commit()

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], "각자 무엇을 할 수 있나")
            msg = _agent_send_and_capture(
                client, room.id, sender_token, f"<@user:{peer_pid}> 차례", "차례",
            )

        meta = msg.get("metadata") or {}
        assert meta.get("peer_redundant") is None
        assert meta.get("peer_depth") == 1

    @pytest.mark.asyncio
    async def test_unmentioned_message_wakes_nobody_so_peer_ask_survives(
        self, ws_env
    ) -> None:
        """#737/#739 — an unaddressed human message wakes no agent, so a
        peer mention that follows must reach its target intact."""
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, _sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], "각자 무엇을 할 수 있나")
            msg = _agent_send_and_capture(
                client, room.id, sender_token, f"<@user:{peer_pid}> 의견 부탁", "의견 부탁",
            )

        meta = msg.get("metadata") or {}
        assert f"<@user:{peer_pid}>" in msg["content"]
        assert meta.get("peer_redundant") is None
        assert meta.get("peer_depth") == 1
        assert app.state.peer_handoff_budget.woken(room.id) == frozenset()


class TestAgentEveryone:
    """#739 — an agent's ``@everyone`` expands into peer mentions and is
    therefore held to the same depth/budget as any other peer ask."""

    @pytest.mark.asyncio
    async def test_agent_everyone_is_a_peer_query_excluding_itself(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, sender_pid, peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], "새 질문")
            msg = _agent_send_and_capture(
                client, room.id, sender_token, "@everyone 확인 부탁", "확인 부탁",
            )

        meta = msg.get("metadata") or {}
        assert meta.get("mentions") == [
            {"type": "user", "id": peer_pid, "via": "everyone"}
        ]
        assert meta.get("peer_depth") == 1
        assert meta.get("kind") == "peer_query"
        assert sender_pid not in {m["id"] for m in meta["mentions"]}

    @pytest.mark.asyncio
    async def test_second_agent_everyone_in_same_turn_is_blocked(self, ws_env) -> None:
        app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
        sender_token, _sender_pid, _peer_pid = await _seed_sender_and_peer(sf, room)

        with TestClient(app) as client:
            _user_send(client, room.id, ws_env["token"], "새 질문")
            _agent_send_and_capture(
                client, room.id, sender_token, "@everyone 첫 요청", "첫 요청",
            )
            msg = _agent_send_and_capture(
                client, room.id, sender_token, "@everyone 두 번째", "두 번째",
            )

        meta = msg.get("metadata") or {}
        assert meta.get("peer_blocked") is True
        assert "mentions" not in meta
