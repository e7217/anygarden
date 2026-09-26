"""Tests for WebSocket connection, messaging, and protocol handling."""

from __future__ import annotations

import json
import secrets
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, Base, Participant, Project, Room, RoomSharedFile, User
from anygarden.db.repository import append_message
from anygarden.ws.manager import ConnectionManager
from anygarden.ws.protocol import (
    ErrorOut,
    MessageOut,
    SendFrame,
    TypingFrame,
    parse_incoming,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def ws_env(config: AnygardenSettings):
    """Set up a full app with a seeded user, room, and participant.

    Yields a dict with keys: app, config, user, room, participant, token.
    """
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    db = session_factory()
    try:
        user = User(email="ws@test.com", password_hash="x")
        db.add(user)
        await db.flush()

        project = Project(name="ws-proj")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="ws-room")
        db.add(room)
        await db.flush()

        participant = Participant(room_id=room.id, user_id=user.id, role="member")
        db.add(participant)
        await db.flush()

        token = create_user_token(user.id, user.email, False, secret=config.jwt_secret)

        # We need to commit so data is visible to the app session
        await db.commit()

        # Refresh to get the committed state
        await db.refresh(user)
        await db.refresh(room)
        await db.refresh(participant)

        app = create_app(config)
        # Override lifespan-created engine with our seeded engine
        app.state.config = config
        app.state.engine = engine
        app.state.session_factory = session_factory

        yield {
            "app": app,
            "config": config,
            "user": user,
            "room": room,
            "participant": participant,
            "token": token,
            "engine": engine,
            "session_factory": session_factory,
        }

    finally:
        # Defensive teardown (#468, supersedes #464/#466): the seeding
        # session stays open across ``yield`` so the in-memory aiosqlite
        # data remains visible to the app — but BOTH teardown steps must
        # swallow the "no active connection" race. The real failure was the
        # session-context ``__aexit__`` rollback (NOT ``engine.dispose()``,
        # which #466 wrapped): a WS test whose handler task is cancelled on
        # TestClient websocket exit can close the shared in-memory
        # connection, so ``close()``'s rollback raises. Neither close nor
        # dispose may turn a passing test into a teardown ERROR.
        try:
            await db.close()
        except Exception:  # pragma: no cover — best-effort teardown cleanup
            pass
        try:
            await engine.dispose()
        except Exception:  # pragma: no cover — best-effort teardown cleanup
            pass


# ── Protocol Frame Tests ──────────────────────────────────────────────


class TestProtocolParsing:
    def test_parse_send_frame(self) -> None:
        f = parse_incoming({"type": "send", "content": "hello"})
        assert isinstance(f, SendFrame)
        assert f.content == "hello"

    def test_parse_typing_frame(self) -> None:
        f = parse_incoming({"type": "typing", "is_typing": True})
        assert isinstance(f, TypingFrame)
        assert f.is_typing is True
        assert f.stage is None
        assert parse_incoming({"type": "typing", "stage": "writing"}).stage == "writing"
        with pytest.raises(ValueError):
            parse_incoming({"type": "typing", "stage": "raw_tool_output"})

    def test_parse_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown frame type"):
            parse_incoming({"type": "bogus"})


# ── ConnectionManager Tests ───────────────────────────────────────────


class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_subscribe_and_unsubscribe(self) -> None:
        mgr = ConnectionManager()
        assert mgr.active_connections == 0

        # We can't use a real WebSocket, but we can test the data structures
        # by using a mock-like approach.
        class FakeWS:
            async def send_text(self, data: str) -> None:
                pass

        ws = FakeWS()  # type: ignore
        await mgr.subscribe("room-1", "p-1", ws)
        assert mgr.active_connections == 1

        await mgr.unsubscribe("p-1")
        assert mgr.active_connections == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent(self) -> None:
        mgr = ConnectionManager()
        # Should not raise
        await mgr.unsubscribe("does-not-exist")

    @pytest.mark.asyncio
    async def test_broadcast_delivers_to_all(self) -> None:
        mgr = ConnectionManager()
        received: list[str] = []

        class FakeWS:
            async def send_text(self, data: str) -> None:
                received.append(data)

        ws1, ws2 = FakeWS(), FakeWS()  # type: ignore
        await mgr.subscribe("room-1", "p-1", ws1)
        await mgr.subscribe("room-1", "p-2", ws2)

        frame = ErrorOut(detail="test broadcast")
        await mgr.broadcast("room-1", frame)
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_send_to_single_participant(self) -> None:
        mgr = ConnectionManager()
        received: list[str] = []

        class FakeWS:
            async def send_text(self, data: str) -> None:
                received.append(data)

        ws = FakeWS()  # type: ignore
        await mgr.subscribe("room-1", "p-1", ws)

        frame = ErrorOut(detail="just for you")
        await mgr.send_to("p-1", frame)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_broadcast_tolerates_closed_connections(self) -> None:
        mgr = ConnectionManager()

        class BrokenWS:
            async def send_text(self, data: str) -> None:
                raise ConnectionError("gone")

        ws = BrokenWS()  # type: ignore
        await mgr.subscribe("room-1", "p-1", ws)

        # Should not raise
        frame = ErrorOut(detail="test")
        await mgr.broadcast("room-1", frame)


# ── WebSocket Endpoint Tests (via ASGI transport) ─────────────────────


class TestWSEndpoint:
    @pytest.mark.asyncio
    async def test_ws_connect_with_subprotocol(self, ws_env) -> None:
        """Connect with proper Sec-WebSocket-Protocol auth."""
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        # Use Starlette TestClient for WebSocket testing (sync)
        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                # Send a message
                ws.send_text(json.dumps({"type": "send", "content": "hello world"}))
                resp = ws.receive_text()
                data = json.loads(resp)
                assert data["type"] == "message"
                assert data["content"] == "hello world"
                assert data["seq"] == 1

    @pytest.mark.asyncio
    async def test_ws_send_and_receive_message(self, ws_env) -> None:
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text(json.dumps({"type": "send", "content": "msg1"}))
                d1 = json.loads(ws.receive_text())
                assert d1["content"] == "msg1"
                assert d1["seq"] == 1

                ws.send_text(json.dumps({"type": "send", "content": "msg2"}))
                d2 = json.loads(ws.receive_text())
                assert d2["content"] == "msg2"
                assert d2["seq"] == 2

    @pytest.mark.asyncio
    async def test_ws_typing_frame(self, ws_env) -> None:
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text(json.dumps({"type": "typing", "is_typing": True}))
                resp = json.loads(ws.receive_text())
                assert resp["type"] == "typing"
                assert resp["is_typing"] is True

                # A human cannot advertise an agent execution stage.
                ws.send_text(json.dumps({"type": "typing", "is_typing": True, "stage": "using_tool"}))
                spoof = json.loads(ws.receive_text())
                assert spoof["stage"] is None

    @pytest.mark.asyncio
    async def test_ws_bad_frame_returns_error(self, ws_env) -> None:
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text("not json at all {{{")
                resp = json.loads(ws.receive_text())
                assert resp["type"] == "error"
                assert "Bad frame" in resp["detail"]

    @pytest.mark.asyncio
    async def test_ws_unknown_frame_type_returns_error(self, ws_env) -> None:
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text(json.dumps({"type": "unknown_type"}))
                resp = json.loads(ws.receive_text())
                assert resp["type"] == "error"

    @pytest.mark.asyncio
    async def test_ws_auth_failure_closes_connection(self, ws_env) -> None:
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        app = ws_env["app"]
        room_id = ws_env["room"].id

        with TestClient(app) as client:
            with pytest.raises(Exception):
                # No subprotocol → auth failure → close
                with client.websocket_connect(f"/ws/rooms/{room_id}") as ws:
                    ws.receive_text()

    @pytest.mark.asyncio
    async def test_ws_non_member_rejected(self, ws_env) -> None:
        """A valid token for a user who is not a member should be rejected."""
        from starlette.testclient import TestClient

        app = ws_env["app"]
        config = ws_env["config"]

        # Create a token for a different user who is NOT in the room
        other_token = create_user_token("other-user-id", "other@test.com", False, secret=config.jwt_secret)
        room_id = ws_env["room"].id

        with TestClient(app) as client:
            with pytest.raises(Exception):
                with client.websocket_connect(
                    f"/ws/rooms/{room_id}",
                    subprotocols=["anygarden.v1", f"bearer.{other_token}"],
                ) as ws:
                    ws.receive_text()

    @pytest.mark.asyncio
    async def test_ws_message_has_participant_id(self, ws_env) -> None:
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id
        participant_id = ws_env["participant"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text(json.dumps({"type": "send", "content": "check pid"}))
                resp = json.loads(ws.receive_text())
                assert resp["participant_id"] == participant_id

    @pytest.mark.asyncio
    async def test_ws_send_with_metadata(self, ws_env) -> None:
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": "with meta",
                    "metadata": {"key": "value"},
                }))
                resp = json.loads(ws.receive_text())
                assert resp["content"] == "with meta"

    @pytest.mark.asyncio
    async def test_ws_canonicalizes_shared_file_references(self, ws_env) -> None:
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        async with ws_env["session_factory"]() as db:
            db.add(
                RoomSharedFile(
                    id="file-1",
                    room_id=room_id,
                    filename="spec.md",
                    storage_name="spec.md",
                    storage_path=f"{room_id}/file-1",
                    sha256="real-sha",
                    size_bytes=12,
                    mime="text/markdown",
                    uploaded_by=None,
                )
            )
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                assert json.loads(ws.receive_text())["type"] == "welcome"
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": "$spec.md review",
                    "metadata": {
                        "references": [
                            {
                                "type": "shared_file",
                                "id": "file-1",
                                "name": "spoofed.md",
                                "storage_name": "../bad",
                                "sha256": "fake",
                                "origin": "inline",
                            }
                        ]
                    },
                }))
                msg = json.loads(ws.receive_text())

        assert msg["metadata"]["references"] == [
            {
                "type": "shared_file",
                "id": "file-1",
                "name": "spec.md",
                "storage_name": "spec.md",
                "sha256": "real-sha",
                "origin": "inline",
            }
        ]

    @pytest.mark.asyncio
    async def test_ws_rejects_cross_room_shared_file_reference(self, ws_env) -> None:
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        async with ws_env["session_factory"]() as db:
            other_room = Room(project_id=ws_env["room"].project_id, name="other")
            db.add(other_room)
            await db.flush()
            db.add(
                RoomSharedFile(
                    id="other-file",
                    room_id=other_room.id,
                    filename="secret.md",
                    storage_name="secret.md",
                    storage_path=f"{other_room.id}/other-file",
                    sha256="other-sha",
                    size_bytes=12,
                    mime="text/markdown",
                    uploaded_by=None,
                )
            )
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                assert json.loads(ws.receive_text())["type"] == "welcome"
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": "bad ref",
                    "metadata": {
                        "references": [
                            {"type": "shared_file", "id": "other-file"}
                        ]
                    },
                }))
                err = json.loads(ws.receive_text())

        assert err["type"] == "error"
        assert err["detail"] == "Invalid shared file reference"


# ── Since-Seq Recovery Tests ──────────────────────────────────────────


class TestSinceSeqRecovery:
    @pytest.mark.asyncio
    async def test_since_seq_replays_missed_messages(self, ws_env) -> None:
        """Pre-seed messages then connect with since_seq to verify replay."""
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id
        participant_id = ws_env["participant"].id
        session_factory = ws_env["session_factory"]

        # Pre-seed 3 messages
        async with session_factory() as db:
            for i in range(3):
                await append_message(db, room_id, participant_id, f"pre-{i}")
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}?since_seq=1",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                # Should receive msgs with seq > 1 (i.e., seq 2 and 3)
                r1 = json.loads(ws.receive_text())
                r2 = json.loads(ws.receive_text())
                assert r1["seq"] == 2
                assert r2["seq"] == 3

    @pytest.mark.asyncio
    async def test_since_seq_replays_more_than_one_page(self, ws_env) -> None:
        """Item 7 (#445): on-connect replay must page past the
        ``replay_since_seq`` default cap (50) instead of silently
        truncating. Seed 130 messages after the agent's ``since_seq``
        and assert every one is replayed in seq order, not just the
        first page.
        """
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id
        participant_id = ws_env["participant"].id
        session_factory = ws_env["session_factory"]

        # One anchor message (seq 1) marks where the agent last was;
        # ``since_seq`` must be > 0 for the handler to replay at all.
        # Then 130 messages (seq 2..131) land while the agent is away —
        # far past the ``replay_since_seq`` default cap of 50.
        missed = 130
        async with session_factory() as db:
            await append_message(db, room_id, participant_id, "anchor")
            for i in range(missed):
                await append_message(db, room_id, participant_id, f"msg-{i}")
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}?since_seq=1",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                seqs = []
                for _ in range(missed):
                    frame = json.loads(ws.receive_text())
                    seqs.append(frame["seq"])

        # Every missed message replayed, ascending, nothing dropped at 50.
        assert len(seqs) == missed
        assert seqs == list(range(2, missed + 2))


class TestPresenceBroadcast:
    """#54 — ConnectionManager must publish presence_update frames
    on subscribe/unsubscribe so other subscribers in the same room
    see the participant flip online/offline in near real time."""

    @pytest.mark.asyncio
    async def test_subscribe_emits_presence_update_online(self, ws_env) -> None:
        """When a second participant subscribes, the first one's WS
        must receive a presence_update(online=True) frame."""
        from starlette.testclient import TestClient

        app = ws_env["app"]
        config = ws_env["config"]
        token = ws_env["token"]
        room = ws_env["room"]
        session_factory = ws_env["session_factory"]

        # Seed a second user + participant so two distinct WS
        # sessions can observe one another's presence updates.
        async with session_factory() as db:
            other = User(email="ws2@test.com", password_hash="x")
            db.add(other)
            await db.flush()
            other_part = Participant(
                room_id=room.id, user_id=other.id, role="member"
            )
            db.add(other_part)
            await db.commit()
            await db.refresh(other)

        other_token = create_user_token(
            other.id, other.email, False, secret=config.jwt_secret
        )

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws1:
                welcome1 = json.loads(ws1.receive_text())
                assert welcome1["type"] == "welcome"

                # ``publish`` excludes the subject participant from the
                # broadcast, so ws1 does NOT receive its own subscribe
                # frame. Only the second participant's subscribe is
                # what ws1 observes.
                with client.websocket_connect(
                    f"/ws/rooms/{room.id}",
                    subprotocols=["anygarden.v1", f"bearer.{other_token}"],
                ) as ws2:
                    _ = json.loads(ws2.receive_text())  # welcome2
                    online_frame = json.loads(ws1.receive_text())
                    assert online_frame["type"] == "presence_update"
                    assert online_frame["online"] is True
                    assert online_frame["participant_id"] == other_part.id
                    assert online_frame["room_id"] == room.id

                # ws2 has now disconnected → ws1 should see offline.
                off = json.loads(ws1.receive_text())
                assert off["type"] == "presence_update"
                assert off["online"] is False
                assert off["participant_id"] == other_part.id


class TestWelcomeAgentId:
    """Issue #61 — WelcomeOut must include agent_id for agent connections
    so the agent SDK can gate room_query forwarding to the representative."""

    @pytest.mark.asyncio
    async def test_welcome_user_has_null_agent_id(self, ws_env) -> None:
        """Regular user connections have no agent_id."""
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                # agent_id must be present as None for users (key exists
                # so clients can unconditionally read it).
                assert welcome.get("agent_id") is None

    @pytest.mark.asyncio
    async def test_welcome_agent_includes_agent_id(self, ws_env) -> None:
        """Agent connections receive their agent_id in the welcome frame."""
        from starlette.testclient import TestClient

        from anygarden.auth.token import generate_token, hash_agent_token
        from anygarden.db.models import AgentToken

        app = ws_env["app"]
        sf = ws_env["session_factory"]
        room = ws_env["room"]

        # Seed an agent + participant + token reusing ws_env's DB.
        async with sf() as db:
            agent = Agent(name="welcome-bot", engine="codex", actual_state="running")
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
            await db.refresh(agent)

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{agent_token_plain}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                assert welcome.get("agent_id") == agent.id


class TestWelcomeParticipantsRoster:
    """Issue #221 — welcome must include a roster of the room's
    participants so orchestrator agents can inject the list into their
    LLM system prompt and call ``handoff_to`` with valid UUIDs."""

    @pytest.mark.asyncio
    async def test_welcome_includes_room_roster(self, ws_env) -> None:
        """User and agent participants show up with kind + display_name."""
        from starlette.testclient import TestClient

        app = ws_env["app"]
        sf = ws_env["session_factory"]
        room = ws_env["room"]
        user = ws_env["user"]
        token = ws_env["token"]

        async with sf() as db:
            agent = Agent(name="orch-agent", engine="claude-code", actual_state="running")
            db.add(agent)
            await db.flush()
            db.add(Participant(room_id=room.id, agent_id=agent.id, role="member"))
            await db.commit()
            await db.refresh(agent)

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                roster = welcome.get("participants")
                assert isinstance(roster, list)
                assert len(roster) == 2

                kinds = {e["kind"] for e in roster}
                assert {"user", "agent"} <= kinds

                agent_entry = next(e for e in roster if e["kind"] == "agent")
                user_entry = next(e for e in roster if e["kind"] == "user")
                assert agent_entry["agent_id"] == agent.id
                assert agent_entry["display_name"] == "orch-agent"
                # Each entry has a participant id (UUID in the room).
                assert agent_entry["id"]
                assert user_entry["id"]
                # User entries do not carry ``agent_id``.
                assert user_entry.get("agent_id") is None
                # Display name falls back to the email local-part when
                # ``User.display_name`` is empty (mirrors REST behaviour).
                assert user_entry["display_name"] == user.email.split("@")[0]

    @pytest.mark.asyncio
    async def test_welcome_includes_agent_description(self, ws_env) -> None:
        """#271 — agents' ``description`` flows into ``ParticipantBrief``
        so peers can recognize them by more than name. Users/guests get
        ``None`` because the field is agent-only metadata."""
        from starlette.testclient import TestClient

        app = ws_env["app"]
        sf = ws_env["session_factory"]
        room = ws_env["room"]
        token = ws_env["token"]

        async with sf() as db:
            agent = Agent(
                name="introbot",
                engine="claude-code",
                actual_state="running",
                description="Frontend reviewer with React expertise",
            )
            db.add(agent)
            await db.flush()
            db.add(Participant(room_id=room.id, agent_id=agent.id, role="member"))
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                roster = welcome["participants"]
                agent_entry = next(e for e in roster if e["kind"] == "agent")
                user_entry = next(e for e in roster if e["kind"] == "user")
                assert agent_entry["description"] == "Frontend reviewer with React expertise"
                # User participants never carry agent metadata.
                assert user_entry.get("description") is None


class TestRoomQueryMetadata:
    """Tests for #room mention → room_query metadata attachment."""

    @pytest_asyncio.fixture()
    async def rq_env(self, config: AnygardenSettings):
        """Set up two rooms: source_room and target_room with representative agent."""
        engine = build_engine(config.db_url)
        sf = build_session_factory(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with sf() as db:
            user = User(email="rq@test.com", password_hash="x", display_name="Alice")
            db.add(user)
            await db.flush()

            project = Project(name="rq-proj")
            db.add(project)
            await db.flush()

            # Source room (where user sends message)
            source_room = Room(project_id=project.id, name="design-room")
            db.add(source_room)
            await db.flush()

            # Target room (mentioned via #room)
            agent = Agent(name="rep-bot", engine="codex", actual_state="running")
            db.add(agent)
            await db.flush()

            target_room = Room(
                project_id=project.id,
                name="backend-room",
                representative_agent_id=agent.id,
            )
            db.add(target_room)
            await db.flush()

            # Agent is participant of target room
            db.add(Participant(room_id=target_room.id, agent_id=agent.id, role="member"))
            # User is participant of source room
            user_part = Participant(room_id=source_room.id, user_id=user.id, role="member")
            db.add(user_part)
            await db.flush()

            await db.commit()
            for obj in (user, project, source_room, target_room, agent, user_part):
                await db.refresh(obj)

            token = create_user_token(user.id, user.email, False, secret=config.jwt_secret)

            app = create_app(config)
            app.state.engine = engine
            app.state.session_factory = sf

            yield {
                "app": app,
                "token": token,
                "source_room": source_room,
                "target_room": target_room,
                "agent": agent,
                "session_factory": sf,
            }

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_room_mention_attaches_room_query(self, rq_env) -> None:
        """Mentioning #room with a representative attaches room_query metadata."""
        from starlette.testclient import TestClient

        app = rq_env["app"]
        token = rq_env["token"]
        source = rq_env["source_room"]
        target = rq_env["target_room"]

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{source.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"

                # Send message mentioning target room
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"<#room:{target.id}> API 설계 의견?",
                }))
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "message"
                meta = msg.get("metadata", {})
                assert "room_query" in meta
                assert meta["room_query"]["target_room_id"] == target.id
                assert meta["room_query"]["source_room_id"] == source.id
                # Issue #55: structured UX needs query_id (UUID) +
                # role marker + the originating user's participant_id
                # so the source-room banner can pair the question with
                # the eventual ``room_query_result`` broadcast.
                assert meta["room_query"]["role"] == "question"
                assert isinstance(meta["room_query"]["query_id"], str)
                assert len(meta["room_query"]["query_id"]) >= 16
                assert meta["room_query"]["source_participant_id"] == msg.get(
                    "participant_id"
                )
                # Issue #155 — attach the source user's display_name so
                # the target-room forward badge can render ``↪ #room ·
                # @Alice`` instead of ``@<last-6-hex>``. Target room's
                # ``participants`` map never contains the source-room
                # user, so ``MessageBubble.resolveUser`` always misses
                # without this server-supplied name.
                assert meta["room_query"]["source_participant_name"] == "Alice"
                # Issue #61 — representative_agent_id must be included so
                # only the designated agent forwards [ROOM_QUERY]. Without
                # it every agent in the source room fans out the forward.
                assert meta["room_query"]["representative_agent_id"] == rq_env[
                    "agent"
                ].id

    @pytest.mark.asyncio
    async def test_room_mention_source_name_falls_back_to_email_local_part(
        self, config: AnygardenSettings
    ) -> None:
        """Issue #155 — when User has no display_name, fall back to the
        email local-part (mirrors ``rooms/router.py:290-302``)."""
        from starlette.testclient import TestClient

        engine = build_engine(config.db_url)
        sf = build_session_factory(engine)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with sf() as db:
            # No display_name — should fall back to "noname"
            user = User(email="noname@test.com", password_hash="x")
            db.add(user)
            await db.flush()

            project = Project(name="rq-proj")
            db.add(project)
            await db.flush()

            source_room = Room(project_id=project.id, name="design-room")
            db.add(source_room)
            await db.flush()

            agent = Agent(name="rep-bot", engine="codex", actual_state="running")
            db.add(agent)
            await db.flush()

            target_room = Room(
                project_id=project.id,
                name="backend-room",
                representative_agent_id=agent.id,
            )
            db.add(target_room)
            await db.flush()

            db.add(Participant(room_id=target_room.id, agent_id=agent.id, role="member"))
            db.add(Participant(room_id=source_room.id, user_id=user.id, role="member"))
            await db.commit()
            for obj in (user, source_room, target_room):
                await db.refresh(obj)

            token = create_user_token(
                user.id, user.email, False, secret=config.jwt_secret
            )

            app = create_app(config)
            app.state.engine = engine
            app.state.session_factory = sf

            with TestClient(app) as client:
                with client.websocket_connect(
                    f"/ws/rooms/{source_room.id}",
                    subprotocols=["anygarden.v1", f"bearer.{token}"],
                ) as ws:
                    welcome = json.loads(ws.receive_text())
                    assert welcome["type"] == "welcome"

                    ws.send_text(json.dumps({
                        "type": "send",
                        "content": f"<#room:{target_room.id}> ping",
                    }))
                    msg = json.loads(ws.receive_text())
                    assert msg["type"] == "message"
                    assert msg["metadata"]["room_query"]["source_participant_name"] == "noname"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_agent_sender_does_not_trigger_room_query(self, rq_env) -> None:
        """Regression guard for the infinite forwarding loop. When
        the message comes from an agent identity (which is what
        ``room_query`` adapters do when forwarding the question),
        the server must NOT re-detect the ``#room`` token and
        re-attach ``room_query`` metadata. Otherwise the target
        room's representative would forward again, ad infinitum.
        """
        from starlette.testclient import TestClient

        from anygarden.auth.token import generate_token, hash_agent_token
        from anygarden.db.models import AgentToken, Participant

        app = rq_env["app"]
        agent = rq_env["agent"]
        target = rq_env["target_room"]
        sf = rq_env["session_factory"]

        # Mint an agent token + ensure the agent is a participant of
        # the source-of-this-test room (target_room — agent is its
        # representative and seeded as a participant in rq_env).
        agent_token_plain = generate_token()
        token_hash, lookup_hint = hash_agent_token(agent_token_plain)
        async with sf() as db:
            db.add(AgentToken(
                agent_id=agent.id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            ))
            await db.commit()

        # Sanity: the agent participant in target_room exists from
        # the fixture; we connect WS *as that agent* to target_room.
        # The agent then sends a fresh ``#room`` mention pointing at
        # itself (target_room) — the server must NOT route this.
        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{target.id}",
                subprotocols=["anygarden.v1", f"bearer.{agent_token_plain}"],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"<#room:{target.id}> 의견?",
                }))
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "message"
                meta = msg.get("metadata") or {}
                # Mention parsing still records what the agent wrote,
                # but ``room_query`` MUST NOT have been attached for
                # an agent-originated message.
                assert "room_query" not in meta

    @pytest.mark.asyncio
    async def test_user_typing_room_query_prefix_still_routes(self, rq_env) -> None:
        """A human user typing the literal text ``[ROOM_QUERY]`` in
        their message must NOT have routing silently disabled. The
        agent-identity guard above is enough to stop the loop;
        adding a content-prefix guard would create a confusing UX
        trap where users couldn't tell why their ``#room`` mention
        was ignored.
        """
        from starlette.testclient import TestClient

        app = rq_env["app"]
        token = rq_env["token"]
        source = rq_env["source_room"]
        target = rq_env["target_room"]

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{source.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"[ROOM_QUERY] <#room:{target.id}> 의견?",
                }))
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "message"
                meta = msg.get("metadata") or {}
                # User-typed prefix is just text — routing proceeds.
                assert "room_query" in meta
                assert meta["room_query"]["target_room_id"] == target.id

    @pytest.mark.asyncio
    async def test_room_mention_no_representative_no_metadata(self, rq_env) -> None:
        """Room mention without representative does not attach room_query."""
        from starlette.testclient import TestClient

        app = rq_env["app"]
        token = rq_env["token"]
        source = rq_env["source_room"]
        sf = rq_env["session_factory"]

        # Create a room without representative
        async with sf() as db:
            norep = Room(project_id=source.project_id, name="no-rep-room")
            db.add(norep)
            await db.commit()
            await db.refresh(norep)

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{source.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"<#room:{norep.id}> 아무 질문",
                }))
                msg = json.loads(ws.receive_text())
                meta = msg.get("metadata", {})
                assert "room_query" not in meta

    @pytest.mark.asyncio
    async def test_room_mention_auto_join_sends_joinroom_to_agent(
        self, rq_env
    ) -> None:
        """Regression guard for issue #50.

        When a user mentions ``<#room:target>`` from a source room
        the representative agent isn't a member of, the server must
        auto-add the agent as a Participant AND push a
        ``JoinRoomOut(room_id=source)`` frame through one of the
        agent's *other* WS sessions, so the SDK opens a subscription
        to the source room in time to receive the upcoming
        ``room_query`` broadcast.

        The original bug only inserted the Participant row — no
        frame — so the agent was a DB member but never subscribed,
        and the broadcast was silently dropped.
        """
        import queue as _q
        import threading

        from starlette.testclient import TestClient

        from anygarden.auth.token import generate_token, hash_agent_token
        from anygarden.db.models import AgentToken

        app = rq_env["app"]
        token = rq_env["token"]
        source = rq_env["source_room"]
        target = rq_env["target_room"]
        agent = rq_env["agent"]
        sf = rq_env["session_factory"]

        agent_token_plain = generate_token()
        token_hash, lookup_hint = hash_agent_token(agent_token_plain)
        async with sf() as db:
            db.add(AgentToken(
                agent_id=agent.id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            ))
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{target.id}",
                subprotocols=["anygarden.v1", f"bearer.{agent_token_plain}"],
            ) as agent_ws:
                agent_welcome = json.loads(agent_ws.receive_text())
                assert agent_welcome["type"] == "welcome"

                with client.websocket_connect(
                    f"/ws/rooms/{source.id}",
                    subprotocols=["anygarden.v1", f"bearer.{token}"],
                ) as user_ws:
                    user_welcome = json.loads(user_ws.receive_text())
                    assert user_welcome["type"] == "welcome"
                    user_ws.send_text(json.dumps({
                        "type": "send",
                        "content": f"<#room:{target.id}> 의견 요청",
                    }))
                    msg = json.loads(user_ws.receive_text())
                    assert msg["type"] == "message"

                # Agent's target-room WS must receive a JoinRoomOut
                # pointing at the *source* room. Wrap in a thread +
                # queue so a missing frame fails fast instead of
                # hanging the test suite.
                received: _q.Queue = _q.Queue()

                def _recv() -> None:
                    try:
                        received.put(("ok", agent_ws.receive_text()))
                    except Exception as exc:  # pragma: no cover
                        received.put(("err", exc))

                threading.Thread(target=_recv, daemon=True).start()
                try:
                    kind, payload = received.get(timeout=3.0)
                except _q.Empty:
                    pytest.fail(
                        "agent WS did not receive JoinRoomOut within 3s "
                        "— auto-join notification is missing"
                    )

                assert kind == "ok", payload
                frame = json.loads(payload)
                assert frame["type"] == "join_room"
                assert frame["room_id"] == source.id

                async with sf() as db:
                    part = (
                        await db.execute(
                            select(Participant).where(
                                Participant.room_id == source.id,
                                Participant.agent_id == agent.id,
                            )
                        )
                    ).scalar_one_or_none()
                    assert part is not None, (
                        "auto-join should have created a Participant "
                        "row for the representative agent"
                    )

    @pytest.mark.asyncio
    async def test_room_mention_offline_agent_sends_error(self, rq_env) -> None:
        """Offline representative agent triggers error frame."""
        from starlette.testclient import TestClient

        app = rq_env["app"]
        token = rq_env["token"]
        source = rq_env["source_room"]
        agent = rq_env["agent"]
        sf = rq_env["session_factory"]

        # Set agent to stopped
        async with sf() as db:
            a = (await db.execute(
                select(Agent).where(Agent.id == agent.id)
            )).scalar_one()
            a.actual_state = "stopped"
            await db.commit()

        target = rq_env["target_room"]
        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{source.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": f"<#room:{target.id}> 질문",
                }))
                # First: the message itself
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "message"
                # Second: error about offline agent
                err = json.loads(ws.receive_text())
                assert err["type"] == "error"
                assert "오프라인" in err["detail"]


class TestContextWindowBroadcast:
    """#148 Part 3 — server-side ingest_only stamping."""

    @pytest.mark.asyncio
    async def test_user_ambient_broadcast_is_not_stamped(
        self, ws_env
    ) -> None:
        """#233 — a human-sent ambient message must NOT be stamped
        with ``ingest_only``. The stamp was originally meant for
        agent-to-agent chatter (#148 Part 3), but a missing sender
        check caused human messages to be demoted to passive
        ingestion, which in turn caused orchestrator rooms to go
        silent once #225 flipped ``context_window_enabled`` on by
        default. Users always expect their plain messages to be
        actionable regardless of the context-window flag."""
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room = ws_env["room"]
        sf = ws_env["session_factory"]

        # Flip the room flag on.
        async with sf() as db:
            r = (
                await db.execute(select(Room).where(Room.id == room.id))
            ).scalar_one()
            r.context_window_enabled = True
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(
                    json.dumps({"type": "send", "content": "잡담 한마디"})
                )
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "message"
                meta = msg.get("metadata") or {}
                # Was previously ``True`` before #233 — the stamp is
                # agent-only now.
                assert "ingest_only" not in meta

    @pytest.mark.asyncio
    async def test_agent_ambient_broadcast_is_stamped_when_enabled(
        self, ws_env
    ) -> None:
        """#148 Part 3 original intent — agent-to-agent ambient
        chatter still picks up ``ingest_only=True`` so peer agents
        absorb it as context instead of replying. This is the
        narrower sender-kind=agent path kept alive after #233 cut
        off the human-sender path.
        """
        from starlette.testclient import TestClient

        from anygarden.auth.token import generate_token, hash_agent_token
        from anygarden.db.models import AgentToken

        app = ws_env["app"]
        room = ws_env["room"]
        sf = ws_env["session_factory"]

        # Flip the flag on and seed a chatty agent participant with
        # its own WS token.
        async with sf() as db:
            r = (
                await db.execute(select(Room).where(Room.id == room.id))
            ).scalar_one()
            r.context_window_enabled = True

            agent = Agent(
                name="chatty-bot",
                engine="codex",
                actual_state="running",
            )
            db.add(agent)
            await db.flush()
            db.add(
                Participant(
                    room_id=room.id, agent_id=agent.id, role="member"
                )
            )
            agent_token_plain = generate_token()
            token_hash, lookup_hint = hash_agent_token(agent_token_plain)
            db.add(
                AgentToken(
                    agent_id=agent.id,
                    token_hash=token_hash,
                    lookup_hint=lookup_hint,
                )
            )
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=[
                    "anygarden.v1",
                    f"bearer.{agent_token_plain}",
                ],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(
                    json.dumps({"type": "send", "content": "잡담 한마디"})
                )
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "message"
                meta = msg.get("metadata") or {}
                assert meta.get("ingest_only") is True

    @pytest.mark.asyncio
    async def test_no_stamp_when_flag_off(self, ws_env) -> None:
        """Rooms with ``context_window_enabled=False`` behave exactly
        as pre-#148: no ``ingest_only`` metadata is attached.

        #225 flipped the server default to True so this test now
        explicitly disables the flag on the fixture room; the old
        assertion that relied on the default being False no longer
        holds.
        """
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room = ws_env["room"]
        sf = ws_env["session_factory"]

        async with sf() as db:
            r = (
                await db.execute(select(Room).where(Room.id == room.id))
            ).scalar_one()
            r.context_window_enabled = False
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                ws.receive_text()
                ws.send_text(
                    json.dumps({"type": "send", "content": "hi"})
                )
                msg = json.loads(ws.receive_text())
                meta = msg.get("metadata") or {}
                assert "ingest_only" not in meta

    @pytest.mark.asyncio
    async def test_direct_mention_bypasses_stamp(self, ws_env) -> None:
        """A direct ``@name`` targets a specific participant — that's
        not ambient, so the stamp must NOT fire even if the flag is
        on. Prevents an addressable message from being silently
        demoted to passive ingestion."""
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room = ws_env["room"]
        sf = ws_env["session_factory"]

        async with sf() as db:
            r = (
                await db.execute(select(Room).where(Room.id == room.id))
            ).scalar_one()
            r.context_window_enabled = True
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                ws.receive_text()
                ws.send_text(
                    json.dumps(
                        {"type": "send", "content": "@bot 핑"}
                    )
                )
                msg = json.loads(ws.receive_text())
                meta = msg.get("metadata") or {}
                # parse_mentions resolves ``@bot`` as a legacy
                # mention → direct addressing → no stamp.
                assert "ingest_only" not in meta

    @pytest.mark.asyncio
    async def test_orchestrator_room_user_send_is_not_stamped(
        self, ws_env
    ) -> None:
        """#233 regression: in an ``orchestrator`` room with
        ``context_window_enabled=True`` and an orchestrator pinned,
        a plain user send must reach peer agents WITHOUT
        ``ingest_only`` so the orchestrator's ``decide_policy`` O1
        rule can fire instead of short-circuiting on rule 4.

        Mirrors the live room4 reproduction captured in the plan:
        before the fix every user turn was stamped and every agent
        silently ingested, leaving the room quiet.
        """
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room = ws_env["room"]
        sf = ws_env["session_factory"]

        # Seed an orchestrator agent participant and flip the
        # room into orchestrator strategy with context-window on.
        async with sf() as db:
            agent = Agent(
                name="alpha-orchestrator",
                engine="codex",
                actual_state="running",
            )
            db.add(agent)
            await db.flush()
            db.add(
                Participant(
                    room_id=room.id, agent_id=agent.id, role="member"
                )
            )

            r = (
                await db.execute(select(Room).where(Room.id == room.id))
            ).scalar_one()
            r.context_window_enabled = True
            r.speaker_strategy = "orchestrator"
            r.orchestrator_agent_id = agent.id
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(
                    json.dumps({"type": "send", "content": "분석해줘"})
                )
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "message"
                meta = msg.get("metadata") or {}
                # Before #233 this was ``True`` and the orchestrator
                # fell through to INGEST_ONLY.
                assert "ingest_only" not in meta

    @pytest.mark.asyncio
    async def test_welcome_carries_agent_opt_out(self, ws_env) -> None:
        """Agent connecting to the WS must receive its own
        ``context_window_opt_out`` in the welcome frame so the SDK
        can cache it for ``decide_policy``."""
        from starlette.testclient import TestClient

        from anygarden.auth.token import generate_token, hash_agent_token
        from anygarden.db.models import AgentToken

        app = ws_env["app"]
        sf = ws_env["session_factory"]
        room = ws_env["room"]

        async with sf() as db:
            agent = Agent(
                name="optout-bot",
                engine="codex",
                actual_state="running",
                context_window_opt_out=True,
            )
            db.add(agent)
            await db.flush()
            db.add(
                Participant(
                    room_id=room.id, agent_id=agent.id, role="member"
                )
            )
            agent_token_plain = generate_token()
            token_hash, lookup_hint = hash_agent_token(agent_token_plain)
            db.add(
                AgentToken(
                    agent_id=agent.id,
                    token_hash=token_hash,
                    lookup_hint=lookup_hint,
                )
            )
            await db.commit()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=[
                    "anygarden.v1",
                    f"bearer.{agent_token_plain}",
                ],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                assert welcome.get("context_window_opt_out") is True


# ── ActivityLog request_id correlation (#222) ────────────────────────
#
# The per-agent ``request_id`` minted on each user send is the key that
# ties ``message_received`` → ``handler_started`` → ``response_sent`` →
# ``handler_finished`` into a single turn. The frontend's ActivityPanel
# groups ActivityLog rows by this id, so two server-side guarantees must
# hold:
#
# 1. ``message_received`` details include ``trigger_message_id`` pointing
#    at the user Message row that woke the agent up — that's the link the
#    UI uses to render "this turn responds to message X".
# 2. When an agent echoes ``metadata.request_id`` back on its response,
#    the stored Message row preserves that id in ``extra_metadata`` — so
#    the message-level replay path can surface the turn id without
#    needing a separate ActivityLog lookup.


class TestActivityLogRequestIdCorrelation:
    @pytest_asyncio.fixture()
    async def corr_env(self, config: AnygardenSettings):
        """User + agent in a shared room, plus a fresh agent WS token.

        Mirrors the rq_env shape but simpler: one room, one user, one
        agent. Yields all the handles tests need to drive both the
        user-send and agent-send code paths.
        """
        from anygarden.auth.token import generate_token, hash_agent_token
        from anygarden.db.models import AgentToken

        engine = build_engine(config.db_url)
        sf = build_session_factory(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with sf() as db:
            user = User(email="corr@test.com", password_hash="x")
            db.add(user)
            await db.flush()

            project = Project(name="corr-proj")
            db.add(project)
            await db.flush()

            room = Room(project_id=project.id, name="corr-room")
            db.add(room)
            await db.flush()

            agent = Agent(
                name="corr-bot", engine="codex", actual_state="running"
            )
            db.add(agent)
            await db.flush()

            db.add(Participant(
                room_id=room.id, user_id=user.id, role="member"
            ))
            db.add(Participant(
                room_id=room.id, agent_id=agent.id, role="member"
            ))

            agent_token_plain = generate_token()
            token_hash, lookup_hint = hash_agent_token(agent_token_plain)
            db.add(AgentToken(
                agent_id=agent.id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            ))
            await db.commit()
            for obj in (user, project, room, agent):
                await db.refresh(obj)

            user_token = create_user_token(
                user.id, user.email, False, secret=config.jwt_secret
            )

            app = create_app(config)
            app.state.engine = engine
            app.state.session_factory = sf

            yield {
                "app": app,
                "user_token": user_token,
                "agent_token": agent_token_plain,
                "user": user,
                "agent": agent,
                "room": room,
                "session_factory": sf,
            }

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_message_received_records_trigger_message_id(
        self, corr_env
    ) -> None:
        """User send must stamp the ``message_received`` ActivityLog
        with the id of the Message row it just wrote — that's the link
        ActivityPanel uses to tie a turn back to the user input."""
        from starlette.testclient import TestClient

        from anygarden.db.models import ActivityLog, Message

        app = corr_env["app"]
        user_token = corr_env["user_token"]
        agent_id = corr_env["agent"].id
        room_id = corr_env["room"].id
        sf = corr_env["session_factory"]

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{user_token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": "hello agent",
                }))
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "message"
                msg_id = msg["id"]

        async with sf() as db:
            stored_msg = (await db.execute(
                select(Message).where(Message.id == msg_id)
            )).scalar_one()
            assert stored_msg.content == "hello agent"

            row = (await db.execute(
                select(ActivityLog).where(
                    ActivityLog.agent_id == agent_id,
                    ActivityLog.event_type == "message_received",
                )
            )).scalar_one()
            assert row.request_id is not None
            assert row.details["trigger_message_id"] == msg_id
            assert row.details["room_id"] == room_id

    @pytest.mark.asyncio
    async def test_agent_response_message_preserves_request_id(
        self, corr_env
    ) -> None:
        """Agent echoes the per-turn ``request_id`` on its response
        frame. The server relays that echo onto ``response_sent``
        ActivityLog (already covered elsewhere) AND — per #222 — must
        also leave it on the stored Message's ``extra_metadata`` so the
        message row itself is self-describing."""
        from starlette.testclient import TestClient

        from anygarden.db.models import Message

        app = corr_env["app"]
        agent_token = corr_env["agent_token"]
        room_id = corr_env["room"].id
        sf = corr_env["session_factory"]

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{agent_token}"],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(json.dumps({
                    "type": "send",
                    "content": "agent reply",
                    "metadata": {"request_id": "rid-echo-test"},
                }))
                resp = json.loads(ws.receive_text())
                assert resp["type"] == "message"
                msg_id = resp["id"]

        async with sf() as db:
            stored = (await db.execute(
                select(Message).where(Message.id == msg_id)
            )).scalar_one()
            assert stored.extra_metadata is not None
            assert stored.extra_metadata["request_id"] == "rid-echo-test"


# ── A→B causal link (#431) ───────────────────────────────────────────
#
# When agent A's reply nominates agent B as the next speaker, the server
# must mint a *tracked* turn for B — and only for B, not a fan-out to the
# whole room (that would flood ActivityLog with phantom orphans). The
# minted ``message_received`` row carries ``parent_request_id`` (= the
# request_id A echoed) so the flow view / trace can draw A→B.


class TestAgentCausalLink:
    @pytest_asyncio.fixture()
    async def make_room(self, config: AnygardenSettings):
        """Factory: build an app + room with N agents under a strategy.

        Returns handles per test; each call gets its own in-memory DB
        (config.db_url is ``sqlite+aiosqlite://``). Agents are added in
        ``agent_names`` order, which is the round-robin rotation order
        (joined_at, id), so the caller controls who index 0 / 1 are.
        """
        from anygarden.auth.token import generate_token, hash_agent_token
        from anygarden.db.models import AgentToken

        engines = []

        async def _make(*, strategy: str, agent_names: list[str]):
            engine = build_engine(config.db_url)
            sf = build_session_factory(engine)
            engines.append(engine)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            tokens: dict[str, str] = {}
            agents: dict[str, str] = {}
            parts: dict[str, str] = {}
            async with sf() as db:
                project = Project(name="cl-proj")
                db.add(project)
                await db.flush()
                room = Room(
                    project_id=project.id,
                    name="cl-room",
                    speaker_strategy=strategy,
                    current_speaker_index=0,
                )
                db.add(room)
                await db.flush()
                for name in agent_names:
                    agent = Agent(
                        name=name, engine="codex", actual_state="running",
                        desired_state="running",
                    )
                    db.add(agent)
                    await db.flush()
                    part = Participant(
                        room_id=room.id, agent_id=agent.id, role="member"
                    )
                    db.add(part)
                    await db.flush()
                    tok = generate_token()
                    th, lh = hash_agent_token(tok)
                    db.add(AgentToken(agent_id=agent.id, token_hash=th, lookup_hint=lh))
                    tokens[name] = tok
                    agents[name] = agent.id
                    parts[name] = part.id
                await db.commit()
                room_id = room.id
            app = create_app(config)
            app.state.engine = engine
            app.state.session_factory = sf
            return {
                "app": app,
                "sf": sf,
                "room_id": room_id,
                "tokens": tokens,
                "agents": agents,
                "parts": parts,
            }

        yield _make
        for e in engines:
            await e.dispose()

    @staticmethod
    def _agent_send(
        app, token: str, room_id: str, content: str, metadata=None, thread_root_id=None
    ):
        """Connect as an agent, send one message, return its id."""
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                ws.receive_text()  # welcome
                frame = {"type": "send", "content": content}
                if thread_root_id is not None:
                    frame["thread_root_id"] = thread_root_id
                if metadata is not None:
                    frame["metadata"] = metadata
                ws.send_text(json.dumps(frame))
                resp = json.loads(ws.receive_text())
                assert resp["type"] == "message"
                return resp["id"]

    @pytest.mark.asyncio
    async def test_nominated_agent_turn_carries_parent_request_id(
        self, make_room
    ) -> None:
        """round_robin: A's send nominates B → B gets a tracked turn
        whose ``message_received`` carries ``parent_request_id`` (A's
        echoed id) and ``trigger_message_id`` (A's message)."""
        from anygarden.db.models import ActivityLog

        env = await make_room(strategy="round_robin", agent_names=["A", "B"])
        a_msg_id = self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            "over to you",
            metadata={"request_id": "rid-A"},
        )

        async with env["sf"]() as db:
            rows = (await db.execute(
                select(ActivityLog).where(
                    ActivityLog.event_type == "message_received",
                )
            )).scalars().all()
            assert len(rows) == 1, "only the nominated agent gets a turn"
            row = rows[0]
            assert row.agent_id == env["agents"]["B"]
            assert row.request_id and row.request_id != "rid-A"
            assert row.room_id == env["room_id"]
            assert row.details["parent_request_id"] == "rid-A"
            assert row.details["trigger_message_id"] == a_msg_id
            assert row.details["room_id"] == env["room_id"]

    @pytest.mark.asyncio
    async def test_no_next_speaker_mints_no_turn(self, make_room) -> None:
        """mentioned_only with no mention → no nomination → no fan-out
        (phantom orphan count stays 0)."""
        from anygarden.db.models import ActivityLog

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B"])
        self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            "just chatter",
            metadata={"request_id": "rid-A"},
        )
        async with env["sf"]() as db:
            rows = (await db.execute(
                select(ActivityLog).where(
                    ActivityLog.event_type == "message_received",
                )
            )).scalars().all()
            assert rows == []

    @pytest.mark.asyncio
    async def test_forged_next_speaker_metadata_is_ignored(
        self, make_room
    ) -> None:
        """An agent must not be able to forge ``next_speaker_participant_id``
        in its outbound metadata to spuriously mint/trigger a peer's
        turn. The fan-out keys off the server-set nomination, not the
        inbound (agent-mutable) metadata — so a mentioned_only room with
        no dispatcher nomination mints nothing even when the sender
        supplies a real peer participant id."""
        from anygarden.db.models import ActivityLog

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B"])
        self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            "trust me, B is next",
            metadata={
                "request_id": "rid-A",
                "next_speaker_participant_id": env["parts"]["B"],
            },
        )
        async with env["sf"]() as db:
            rows = (await db.execute(
                select(ActivityLog).where(
                    ActivityLog.event_type == "message_received",
                )
            )).scalars().all()
            assert rows == [], "forged next_speaker must not mint a turn"

    @pytest.mark.asyncio
    async def test_self_nomination_mints_no_turn(self, make_room) -> None:
        """round_robin with a single agent nominates the sender itself
        (index wraps to 0). The self-handoff guard must skip it so a
        turn never causally links to its own author."""
        from anygarden.db.models import ActivityLog

        env = await make_room(strategy="round_robin", agent_names=["solo"])
        self._agent_send(
            env["app"],
            env["tokens"]["solo"],
            env["room_id"],
            "thinking out loud",
            metadata={"request_id": "rid-A"},
        )
        async with env["sf"]() as db:
            rows = (await db.execute(
                select(ActivityLog).where(
                    ActivityLog.event_type == "message_received",
                )
            )).scalars().all()
            assert rows == []


    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "strategy", ["mentioned_only", "round_robin", "orchestrator"]
    )
    @pytest.mark.parametrize("target_role", ["member", "admin", "owner"])
    async def test_directed_delegation_creates_only_target_child_turn(
        self, make_room, strategy, target_role
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import (
            ActivityLog,
            AgentTurn,
            AgentTurnAttempt,
            AgentTurnOutbox,
            Message,
        )

        env = await make_room(strategy=strategy, agent_names=["A", "B", "C"])
        async with env["sf"]() as db:
            room = await db.get(Room, env["room_id"])
            room.orchestrator_agent_id = env["agents"]["A"]
            target = await db.get(Participant, env["parts"]["C"])
            target.role = target_role
            await db.commit()
        delegation_id = str(uuid4())
        message_id = self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            f"[DELEGATED] <@user:{env['parts']['C']}> review the changes",
            metadata={
                "delegation_id": delegation_id,
                "delegation_target_participant_id": env["parts"]["C"],
                "request_id": "parent-request",
                "next_speaker_participant_id": env["parts"]["B"],
            },
        )
        async with env["sf"]() as db:
            turns = (await db.scalars(select(AgentTurn))).all()
            assert len(turns) == 1
            turn = turns[0]
            assert turn.agent_id == env["agents"]["C"]
            assert turn.target_participant_id == env["parts"]["C"]
            assert turn.trigger_message_id == message_id
            assert turn.request_id != "parent-request"
            assert turn.state == "pending"
            attempt = (await db.scalars(select(AgentTurnAttempt))).one()
            assert attempt.turn_id == turn.request_id
            assert attempt.lease_token
            outbox = (await db.scalars(select(AgentTurnOutbox))).one()
            assert outbox.turn_id == turn.request_id
            assert outbox.participant_id == env["parts"]["C"]
            message = await db.get(Message, message_id)
            assert message.extra_metadata["delegation_id"] == delegation_id
            assert (
                message.extra_metadata["next_speaker_participant_id"]
                == env["parts"]["C"]
            )
            event = (
                await db.scalars(
                    select(ActivityLog).where(
                        ActivityLog.event_type == "message_received"
                    )
                )
            ).one()
            assert event.request_id == turn.request_id
            assert event.details["parent_request_id"] == "parent-request"
            assert (
                await db.get(Room, env["room_id"])
            ).next_speaker_participant_id == env["parts"]["C"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("strategy", ["round_robin", "orchestrator"])
    @pytest.mark.parametrize(
        "invalid_case",
        [
            "observer",
            "human_target",
            "cross_room",
            "self",
            "missing_target",
            "null_target",
            "bad_id",
            "missing_id",
            "forged_mentions",
            "mismatched_mention",
            "bad_prefix",
            "empty_task",
            "human_sender",
        ],
    )
    async def test_invalid_directed_delegation_never_falls_back(
        self, make_room, config, strategy, invalid_case
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn, Message
        from starlette.testclient import TestClient

        env = await make_room(strategy=strategy, agent_names=["A", "B", "C"])
        token = env["tokens"]["A"]
        target_pid = env["parts"]["C"]
        async with env["sf"]() as db:
            room = await db.get(Room, env["room_id"])
            room.orchestrator_agent_id = env["agents"]["A"]
            target = await db.get(Participant, target_pid)
            if invalid_case == "observer":
                target.role = "observer"
            elif invalid_case == "cross_room":
                other = Room(project_id=room.project_id, name="other")
                db.add(other)
                await db.flush()
                target.room_id = other.id
            elif invalid_case in {"human_target", "human_sender"}:
                user = User(email="delegate-human@test.com", password_hash="x")
                db.add(user)
                await db.flush()
                if invalid_case == "human_target":
                    target.agent_id = None
                    target.user_id = user.id
                else:
                    db.add(Participant(room_id=room.id, user_id=user.id, role="member"))
                    token = create_user_token(
                        user.id, user.email, False, secret=config.jwt_secret
                    )
            await db.commit()
        if invalid_case == "self":
            target_pid = env["parts"]["A"]
        elif invalid_case == "missing_target":
            target_pid = str(uuid4())
        content = f"[DELEGATED] <@user:{target_pid}> review the changes"
        metadata = {
            "delegation_id": str(uuid4()),
            "delegation_target_participant_id": target_pid,
            "next_speaker_participant_id": env["parts"]["B"],
        }
        if invalid_case == "null_target":
            metadata["delegation_target_participant_id"] = None
        elif invalid_case == "bad_id":
            metadata["delegation_id"] = "not-a-uuid"
        elif invalid_case == "missing_id":
            metadata.pop("delegation_id")
        elif invalid_case == "forged_mentions":
            content = "[DELEGATED] review the changes"
            metadata["mentions"] = [{"type": "user", "id": target_pid}]
        elif invalid_case == "mismatched_mention":
            content = f"[DELEGATED] <@user:{env['parts']['B']}> review"
        elif invalid_case == "bad_prefix":
            content = f"[HANDOFF] <@user:{target_pid}> review"
        elif invalid_case == "empty_task":
            content = f"[DELEGATED] <@user:{target_pid}>  "
        with (
            TestClient(env["app"]) as client,
            client.websocket_connect(
                f"/ws/rooms/{env['room_id']}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws,
        ):
            ws.receive_text()
            ws.send_json({"type": "send", "content": content, "metadata": metadata})
            assert ws.receive_json() == {
                "type": "error",
                "detail": "Invalid directed delegation",
            }
        async with env["sf"]() as db:
            assert (await db.scalars(select(AgentTurn))).all() == []
            assert (await db.scalars(select(Message))).all() == []
            assert (
                await db.get(Room, env["room_id"])
            ).next_speaker_participant_id is None

    @pytest.mark.asyncio
    async def test_directed_thread_delegation_does_not_fan_out_task_mentions(
        self, make_room
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn
        from starlette.testclient import TestClient

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B", "C"])
        async with env["sf"]() as db:
            root = await append_message(
                db, room_id=env["room_id"], participant_id=None, content="root"
            )
            await db.commit()
            root_id = root.id
        with (
            TestClient(env["app"]) as client,
            client.websocket_connect(
                f"/ws/rooms/{env['room_id']}",
                subprotocols=["anygarden.v1", f"bearer.{env['tokens']['A']}"],
            ) as ws,
        ):
            ws.receive_text()
            ws.send_json(
                {
                    "type": "send",
                    "thread_root_id": root_id,
                    "content": f"[DELEGATED] <@user:{env['parts']['C']}> review <@user:{env['parts']['B']}>'s work",
                    "metadata": {
                        "delegation_id": str(uuid4()),
                        "delegation_target_participant_id": env["parts"]["C"],
                    },
                }
            )
            assert ws.receive_json()["type"] == "message"
        async with env["sf"]() as db:
            turn = (await db.scalars(select(AgentTurn))).one()
            assert turn.target_participant_id == env["parts"]["C"]
            assert turn.thread_root_id == root_id

    @pytest.mark.asyncio
    async def test_directed_delegation_delivers_one_leased_frame_to_live_target(
        self, make_room
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn, AgentTurnOutbox
        from starlette.testclient import TestClient

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B", "C"])
        delegation_id = str(uuid4())
        with TestClient(env["app"]) as client:

            def connect(name):
                return client.websocket_connect(
                    f"/ws/rooms/{env['room_id']}?generation=0&ready=1",
                    subprotocols=["anygarden.v1", f"bearer.{env['tokens'][name]}"],
                )

            with (
                connect("A") as sender,
                connect("B") as bystander,
                connect("C") as target,
            ):
                for ws in (sender, bystander, target):
                    assert ws.receive_json()["type"] == "welcome"
                    ready = ws.receive_json()
                    while ready["type"] == "presence_update":
                        ready = ws.receive_json()
                    assert ready == {"type": "room_ready", "room_id": env["room_id"]}
                sender.send_json(
                    {
                        "type": "send",
                        "content": f"[DELEGATED] <@user:{env['parts']['C']}> review",
                        "metadata": {
                            "delegation_id": delegation_id,
                            "delegation_target_participant_id": env["parts"]["C"],
                        },
                    }
                )

                def receive_message(ws):
                    frame = ws.receive_json()
                    while frame["type"] == "presence_update":
                        frame = ws.receive_json()
                    assert frame["type"] == "message", frame
                    return frame

                sent = receive_message(sender)
                ambient = receive_message(bystander)
                invocation = receive_message(target)
                assert invocation["id"] == sent["id"] == ambient["id"]
                assert "request_id" not in ambient["metadata"]
                md = invocation["metadata"]
                assert md["delegation_id"] == delegation_id
                assert md["request_id"]
                assert md["turn_lease"]
                assert md["turn_attempt"] == 1
                assert md["turn_generation"] == 0
                # Finish the sender's outbox transaction before another
                # socket touches this fixture's single SQLite connection.
                sender.send_json({"type": "delivery-complete-check"})
                assert sender.receive_json()["type"] == "error"
                # A round trip is a barrier: no raw duplicate may be queued
                # before this next frame on the target's socket.
                target.send_json({"type": "typing", "is_typing": False})
                assert target.receive_json()["type"] == "typing"
                for ws in (target, bystander, sender):
                    ws.close()

                async def wait_disconnected():
                    import anyio

                    manager = env["app"].state.connection_manager
                    with anyio.fail_after(5):
                        while any(
                            [
                                await manager.is_connected(pid)
                                for pid in env["parts"].values()
                            ]
                        ):
                            await anyio.sleep(0.001)

                client.portal.call(wait_disconnected)
        async with env["sf"]() as db:
            turn = (await db.scalars(select(AgentTurn))).one()
            assert turn.request_id == md["request_id"]
            outbox = (await db.scalars(select(AgentTurnOutbox))).one()
            assert outbox.state == "delivered"
            assert outbox.delivery_count == 1

    @pytest.mark.asyncio
    async def test_directed_delegation_replay_does_not_duplicate_pending_delivery(
        self, make_room
    ) -> None:
        from uuid import uuid4

        from anygarden.turns.service import deliver_pending_outbox
        from starlette.testclient import TestClient

        env = await make_room(strategy="mentioned_only", agent_names=["A", "C"])
        async with env["sf"]() as db:
            await append_message(
                db, room_id=env["room_id"], participant_id=None, content="baseline"
            )
            await db.commit()
        message_id = self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            f"[DELEGATED] <@user:{env['parts']['C']}> review",
            metadata={
                "delegation_id": str(uuid4()),
                "delegation_target_participant_id": env["parts"]["C"],
            },
        )
        with (
            TestClient(env["app"]) as client,
            client.websocket_connect(
                f"/ws/rooms/{env['room_id']}?since_seq=1&generation=0&ready=1",
                subprotocols=["anygarden.v1", f"bearer.{env['tokens']['C']}"],
            ) as ws,
        ):
            assert ws.receive_json()["type"] == "welcome"
            invocation = ws.receive_json()
            assert invocation["id"] == message_id
            assert invocation["metadata"]["turn_lease"]
            assert ws.receive_json() == {
                "type": "room_ready",
                "room_id": env["room_id"],
            }

            async def deliver():
                return await deliver_pending_outbox(
                    env["sf"],
                    env["app"].state.connection_manager,
                    participant_ids=[env["parts"]["C"]],
                )

            assert client.portal.call(deliver) == 0

    @pytest.mark.asyncio
    async def test_concurrent_directed_requests_keep_distinct_child_leases(
        self, make_room
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn, AgentTurnAttempt
        from anygarden.orchestration.rules import PeerHandoffBudget

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B", "C"])
        env["app"].state.peer_handoff_budget = PeerHandoffBudget()
        for target_name in ("B", "C"):
            self._agent_send(
                env["app"],
                env["tokens"]["A"],
                env["room_id"],
                f"[DELEGATED] <@user:{env['parts'][target_name]}> review",
                metadata={
                    "delegation_id": str(uuid4()),
                    "delegation_target_participant_id": env["parts"][target_name],
                },
            )
        async with env["sf"]() as db:
            turns = (await db.scalars(select(AgentTurn))).all()
            assert {turn.target_participant_id for turn in turns} == {
                env["parts"]["B"],
                env["parts"]["C"],
            }
            assert len({turn.request_id for turn in turns}) == 2
            attempts = (await db.scalars(select(AgentTurnAttempt))).all()
            assert len({attempt.lease_token for attempt in attempts}) == 2

    @pytest.mark.asyncio
    async def test_legacy_delegation_metadata_keeps_existing_routing(
        self, make_room
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B"])
        self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            "[DELEGATED] legacy request",
            metadata={"delegation_id": str(uuid4())},
        )
        async with env["sf"]() as db:
            assert (await db.scalars(select(AgentTurn))).all() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "strategy", ["mentioned_only", "round_robin", "orchestrator"]
    )
    @pytest.mark.parametrize(
        "outcome",
        ["ok", "failed", "timeout", "cancelled", "rejected", "retry_exhausted"],
    )
    @pytest.mark.parametrize("in_thread", [False, True])
    async def test_terminal_delegation_result_never_wakes_another_agent(
        self, make_room, strategy, outcome, in_thread
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn, Message
        from anygarden.orchestration.rules import PeerHandoffBudget

        env = await make_room(strategy=strategy, agent_names=["A", "B"])
        budget = PeerHandoffBudget()
        env["app"].state.peer_handoff_budget = budget
        remaining_before = budget.remaining(env["room_id"])
        root_id = None
        async with env["sf"]() as db:
            room = await db.get(Room, env["room_id"])
            room.orchestrator_agent_id = env["agents"]["A"]
            if in_thread:
                root = await append_message(
                    db, room_id=room.id, participant_id=None, content="root"
                )
                root_id = root.id
            await db.commit()
        content = f"Result mentions <@user:{env['parts']['B']}> for context only"
        if strategy == "orchestrator" and not in_thread:
            # Exercise both the explicit handoff and unaddressed moderator
            # fallback, which must also remain idle for terminal results.
            content = (
                f"[HANDOFF] <@user:{env['parts']['B']}> result"
                if outcome == "ok"
                else "Delegated work finished"
            )
        message_id = self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            content,
            metadata={"delegation_id": str(uuid4()), "delegation_outcome": outcome},
            thread_root_id=root_id,
        )
        assert budget.remaining(env["room_id"]) == remaining_before
        async with env["sf"]() as db:
            assert (await db.scalars(select(AgentTurn))).all() == []
            message = await db.get(Message, message_id)
            assert message.extra_metadata["delegation_outcome"] == outcome
            assert (
                await db.get(Room, env["room_id"])
            ).next_speaker_participant_id is None

    @pytest.mark.asyncio
    async def test_terminal_delegation_result_completes_its_leased_parent(
        self, make_room
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn, AgentTurnAttempt, Message
        from anygarden.turns.service import create_turn

        env = await make_room(strategy="round_robin", agent_names=["A", "B"])
        async with env["sf"]() as db:
            trigger = await append_message(
                db, room_id=env["room_id"], participant_id=None, content="parent task"
            )
            turn = await create_turn(
                db,
                room_id=env["room_id"],
                participant_id=env["parts"]["A"],
                agent_id=env["agents"]["A"],
                trigger_message_id=trigger.id,
            )
            attempt = (await db.scalars(select(AgentTurnAttempt))).one()
            proof = {
                "request_id": turn.request_id,
                "turn_attempt": attempt.attempt_number,
                "turn_generation": attempt.generation,
                "turn_lease": attempt.lease_token,
                "delegation_id": str(uuid4()),
                "delegation_outcome": "ok",
            }
            await db.commit()
        message_id = self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            "Delegated work finished",
            metadata=proof,
        )
        async with env["sf"]() as db:
            turn = (await db.scalars(select(AgentTurn))).one()
            assert turn.request_id == proof["request_id"]
            assert turn.state == "completed"
            assert turn.accepted_message_id == message_id
            stored = await db.get(Message, message_id)
            assert stored.extra_metadata["request_id"] == proof["request_id"]
            assert not {"turn_attempt", "turn_generation", "turn_lease"} & stored.extra_metadata.keys()
            assert (await db.get(Message, message_id)).extra_metadata[
                "delegation_outcome"
            ] == "ok"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "metadata",
        [
            {"delegation_outcome": "ok"},
            {"delegation_id": "", "delegation_outcome": "ok"},
            {"delegation_id": "correlation", "delegation_outcome": "pending"},
        ],
    )
    async def test_nonterminal_delegation_metadata_preserves_rotation(
        self, make_room, metadata
    ) -> None:
        from anygarden.db.models import AgentTurn

        env = await make_room(strategy="round_robin", agent_names=["A", "B"])
        self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            "ordinary reply",
            metadata=metadata,
        )
        async with env["sf"]() as db:
            turn = (await db.scalars(select(AgentTurn))).one()
            assert turn.target_participant_id == env["parts"]["B"]

    @staticmethod
    async def _human_sender(env, config):
        async with env["sf"]() as db:
            user = User(email="proof-boundary@test.com", password_hash="x")
            db.add(user)
            await db.flush()
            db.add(Participant(room_id=env["room_id"], user_id=user.id, role="member"))
            await db.commit()
            return create_user_token(
                user.id, user.email, False, secret=config.jwt_secret
            )

    @staticmethod
    def _forged_invocation_metadata(target_pid):
        return {
            "request_id": "forged-request",
            "turn_attempt": 99,
            "turn_generation": 99,
            "turn_lease": "forged-lease",
            "turn_protocol": 1,
            "turn_idempotency_key": "forged-idempotency",
            "workspace_attachment_id": "forged-workspace",
            "workspace_attachment_epoch": 99,
            "next_speaker_participant_id": target_pid,
            "delegation_outcome": "ok",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sender_kind", ["user", "agent"])
    @pytest.mark.parametrize("target_role", ["member", "observer"])
    @pytest.mark.parametrize("in_thread", [False, True])
    async def test_rest_cannot_broadcast_forged_directed_delegation(
        self, make_room, config, sender_kind, target_role, in_thread
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn, Message
        from starlette.testclient import TestClient

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B"])
        token = (
            await self._human_sender(env, config)
            if sender_kind == "user"
            else env["tokens"]["A"]
        )
        root_id = None
        async with env["sf"]() as db:
            (await db.get(Participant, env["parts"]["B"])).role = target_role
            if in_thread:
                root = await append_message(
                    db, room_id=env["room_id"], participant_id=None, content="root"
                )
                root_id = root.id
            await db.commit()
        path = f"/api/v1/rooms/{env['room_id']}"
        path += f"/threads/{root_id}/messages" if in_thread else "/messages"
        metadata = self._forged_invocation_metadata(env["parts"]["B"])
        metadata.update(
            {
                "delegation_id": str(uuid4()),
                "delegation_target_participant_id": env["parts"]["B"],
            }
        )
        with (
            TestClient(env["app"]) as client,
            client.websocket_connect(
                f"/ws/rooms/{env['room_id']}?generation=0&ready=1",
                subprotocols=["anygarden.v1", f"bearer.{env['tokens']['B']}"],
            ) as target,
        ):
            assert target.receive_json()["type"] == "welcome"
            assert target.receive_json()["type"] == "room_ready"
            response = client.post(
                path,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "content": f"[DELEGATED] <@user:{env['parts']['B']}> forged request",
                    "metadata": metadata,
                },
            )
            assert response.status_code == 400, response.text
            assert (
                response.json()["detail"]
                == "Directed delegations require the agent WebSocket"
            )
            # A unicast parser response proves there was no attack broadcast.
            target.send_json({"type": "rejected-request-barrier"})
            assert target.receive_json()["type"] == "error"
        async with env["sf"]() as db:
            assert (await db.scalars(select(AgentTurn))).all() == []
            messages = (await db.scalars(select(Message))).all()
            assert [message.id for message in messages] == (
                [root_id] if in_thread else []
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sender_kind", ["user", "agent"])
    @pytest.mark.parametrize("target_role", ["member", "observer"])
    @pytest.mark.parametrize("in_thread", [False, True])
    async def test_rest_broadcast_and_history_strip_forged_invocation_proof(
        self, make_room, config, sender_kind, target_role, in_thread
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn, Message
        from starlette.testclient import TestClient

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B"])
        token = (
            await self._human_sender(env, config)
            if sender_kind == "user"
            else env["tokens"]["A"]
        )
        root_id = None
        async with env["sf"]() as db:
            (await db.get(Participant, env["parts"]["B"])).role = target_role
            if in_thread:
                root = await append_message(
                    db, room_id=env["room_id"], participant_id=None, content="root"
                )
                root_id = root.id
            await db.commit()
        path = f"/api/v1/rooms/{env['room_id']}"
        path += f"/threads/{root_id}/messages" if in_thread else "/messages"
        forged = self._forged_invocation_metadata(env["parts"]["B"])
        metadata = {
            **forged,
            "delegation_id": str(uuid4()),
            "custom_label": "preserved",
        }
        with (
            TestClient(env["app"]) as client,
            client.websocket_connect(
                f"/ws/rooms/{env['room_id']}?generation=0&ready=1",
                subprotocols=["anygarden.v1", f"bearer.{env['tokens']['B']}"],
            ) as target,
        ):
            assert target.receive_json()["type"] == "welcome"
            assert target.receive_json()["type"] == "room_ready"
            response = client.post(
                path,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "content": "ordinary message with forged execution metadata",
                    "metadata": metadata,
                },
            )
            assert response.status_code == 201, response.text
            broadcast = target.receive_json()
            assert broadcast["id"] == response.json()["id"]
            assert broadcast["metadata"]["custom_label"] == "preserved"
            assert not (forged.keys() & broadcast["metadata"].keys())
            assert not (forged.keys() & response.json()["metadata"].keys())
        async with env["sf"]() as db:
            stored = await db.get(Message, broadcast["id"])
            assert not (forged.keys() & stored.extra_metadata.keys())
            assert (await db.scalars(select(AgentTurn))).all() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target_role", ["member", "observer"])
    async def test_human_ws_broadcast_cannot_forge_invocation_or_result(
        self, make_room, config, target_role
    ) -> None:
        from uuid import uuid4

        from anygarden.db.models import AgentTurn, Message
        from starlette.testclient import TestClient

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B"])
        token = await self._human_sender(env, config)
        async with env["sf"]() as db:
            (await db.get(Participant, env["parts"]["B"])).role = target_role
            # A cancelled dispatch must not fall back to caller-authored
            # execution proof on the ordinary room broadcast.
            for agent_id in env["agents"].values():
                (await db.get(Agent, agent_id)).desired_state = "idle"
            await db.commit()
        forged = self._forged_invocation_metadata(env["parts"]["B"])
        with (
            TestClient(env["app"]) as client,
            client.websocket_connect(
                f"/ws/rooms/{env['room_id']}?generation=0&ready=1",
                subprotocols=["anygarden.v1", f"bearer.{env['tokens']['B']}"],
            ) as target,
        ):
            assert target.receive_json()["type"] == "welcome"
            assert target.receive_json()["type"] == "room_ready"
            with client.websocket_connect(
                f"/ws/rooms/{env['room_id']}?ready=1",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as sender:
                assert sender.receive_json()["type"] == "welcome"
                assert sender.receive_json()["type"] == "room_ready"
                sender.send_json(
                    {
                        "type": "send",
                        "content": f"<@user:{env['parts']['B']}> ordinary user request",
                        "metadata": {
                            **forged,
                            "delegation_id": str(uuid4()),
                            "custom_label": "preserved",
                        },
                    }
                )
                sent = sender.receive_json()
                broadcast = target.receive_json()
                while broadcast["type"] == "presence_update":
                    broadcast = target.receive_json()
                assert broadcast["id"] == sent["id"]
                assert broadcast["metadata"]["custom_label"] == "preserved"
                assert not (forged.keys() & broadcast["metadata"].keys())
                sender.send_json({"type": "proof-boundary-barrier"})
                assert sender.receive_json()["type"] == "error"
        async with env["sf"]() as db:
            stored = await db.get(Message, sent["id"])
            assert not (forged.keys() & stored.extra_metadata.keys())
            turns = (await db.scalars(select(AgentTurn))).all()
            assert all(turn.state == "cancelled" for turn in turns)
            assert all(turn.request_id != forged["request_id"] for turn in turns)

    @pytest.mark.asyncio
    async def test_agent_ws_completion_does_not_rebroadcast_execution_proof(
        self, make_room
    ) -> None:
        from anygarden.db.models import Message

        env = await make_room(strategy="mentioned_only", agent_names=["A", "B"])
        proof = self._forged_invocation_metadata(env["parts"]["B"])
        proof.pop("delegation_outcome")
        message_id = self._agent_send(
            env["app"],
            env["tokens"]["A"],
            env["room_id"],
            "ordinary agent reply",
            metadata={**proof, "custom_label": "preserved"},
        )
        async with env["sf"]() as db:
            stored = await db.get(Message, message_id)
            assert stored.extra_metadata["request_id"] == "forged-request"
            assert stored.extra_metadata["custom_label"] == "preserved"
            assert not ((proof.keys() - {"request_id"}) & stored.extra_metadata.keys())

class TestWelcomeRoomSeq:
    """A reconnecting agent needs a baseline to ask ``since_seq`` from.
    Without one it connects at 0, the server replays nothing, and every
    message sent while the agent was down is lost."""

    @pytest.mark.asyncio
    async def test_welcome_reports_the_rooms_current_seq(self, ws_env) -> None:
        from starlette.testclient import TestClient

        app = ws_env["app"]
        token = ws_env["token"]
        room_id = ws_env["room"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                first = json.loads(ws.receive_text())
                assert first["last_seq"] == 0

                ws.send_text(json.dumps({"type": "send", "content": "msg1"}))
                assert json.loads(ws.receive_text())["seq"] == 1

            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ) as ws:
                assert json.loads(ws.receive_text())["last_seq"] == 1
