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

    async with session_factory() as db:
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

    await engine.dispose()


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
