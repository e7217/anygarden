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

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, Base, Participant, Project, Room, User
from doorae.db.repository import append_message
from doorae.ws.manager import ConnectionManager
from doorae.ws.protocol import (
    ErrorOut,
    MessageOut,
    SendFrame,
    TypingFrame,
    parse_incoming,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def ws_env(config: DooraeSettings):
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
                    subprotocols=["doorae.v1", f"bearer.{other_token}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                # Should receive msgs with seq > 1 (i.e., seq 2 and 3)
                r1 = json.loads(ws.receive_text())
                r2 = json.loads(ws.receive_text())
                assert r1["seq"] == 2
                assert r2["seq"] == 3


class TestRoomQueryMetadata:
    """Tests for #room mention → room_query metadata attachment."""

    @pytest_asyncio.fixture()
    async def rq_env(self, config: DooraeSettings):
        """Set up two rooms: source_room and target_room with representative agent."""
        engine = build_engine(config.db_url)
        sf = build_session_factory(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with sf() as db:
            user = User(email="rq@test.com", password_hash="x")
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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

        from doorae.auth.token import generate_token, hash_agent_token
        from doorae.db.models import AgentToken, Participant

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
                subprotocols=["doorae.v1", f"bearer.{agent_token_plain}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
                subprotocols=["doorae.v1", f"bearer.{token}"],
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
