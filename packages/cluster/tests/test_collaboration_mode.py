# ruff: noqa: F811
"""Integration tests for the per-agent collaboration_mode safety net (#279).

Covers the WS-handler wiring of ``peer_depth``, ``kind``, and the
``PeerHandoffBudget`` cap. The orchestration helpers themselves are
unit-tested in ``test_orchestration.py``; this file exercises the
end-to-end stamping and strip behaviour through the real handler.

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


class TestCollaborationModeWelcome:
    """Issue #279 §2 — welcome frame surfaces ``my_collaboration_mode``
    so the agent SDK can decide whether to append the peer-mention
    usage hint to the LLM system prompt."""

    @pytest.mark.asyncio
    async def test_welcome_default_collaboration_mode_is_solo(
        self, ws_env
    ) -> None:
        """Pre-#279 agents and freshly-created agents default to
        ``solo``; the welcome must reflect that explicitly so the SDK
        cache doesn't carry a stale value across reconnects."""
        app = ws_env["app"]
        sf = ws_env["session_factory"]
        room = ws_env["room"]

        async with sf() as db:
            agent = Agent(
                name="solo-bot", engine="codex", actual_state="running"
            )
            db.add(agent)
            await db.flush()
            db.add(Participant(room_id=room.id, agent_id=agent.id, role="member"))
            agent_token_plain = generate_token()
            token_hash, lookup_hint = hash_agent_token(agent_token_plain)
            db.add(AgentToken(
                agent_id=agent.id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            ))
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{agent_token_plain}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                assert welcome.get("my_collaboration_mode") == "solo"

    @pytest.mark.asyncio
    async def test_welcome_collaborative_agent_surfaces_mode(
        self, ws_env
    ) -> None:
        """Agents flipped to ``collaborative`` see the new mode in
        their welcome frame on the next connect."""
        app = ws_env["app"]
        sf = ws_env["session_factory"]
        room = ws_env["room"]

        async with sf() as db:
            agent = Agent(
                name="collab-bot",
                engine="codex",
                actual_state="running",
                collaboration_mode="collaborative",
            )
            db.add(agent)
            await db.flush()
            db.add(Participant(room_id=room.id, agent_id=agent.id, role="member"))
            agent_token_plain = generate_token()
            token_hash, lookup_hint = hash_agent_token(agent_token_plain)
            db.add(AgentToken(
                agent_id=agent.id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            ))
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{agent_token_plain}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome.get("my_collaboration_mode") == "collaborative"


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
                collaboration_mode="collaborative",
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
                collaboration_mode="collaborative",
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
                collaboration_mode="collaborative",
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
                # First user message (resets budget to capacity).
                user_ws.send_text(json.dumps({
                    "type": "send",
                    "content": "Hi everyone",
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
