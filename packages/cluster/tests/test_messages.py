"""Tests for the REST message history endpoint."""

from __future__ import annotations

import json
import re
import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Base, Participant, Project, Room, User
from doorae.db.repository import append_message


@pytest_asyncio.fixture()
async def msg_env(config: DooraeSettings):
    """Set up a full app with a seeded user, room, participant, and messages."""
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(email="msg@test.com", password_hash="x")
        db.add(user)
        await db.flush()

        project = Project(name="msg-proj")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="msg-room")
        db.add(room)
        await db.flush()

        participant = Participant(room_id=room.id, user_id=user.id, role="member")
        db.add(participant)
        await db.flush()

        # Pre-seed a message WITH metadata
        msg_with_meta = await append_message(
            db,
            room_id=room.id,
            participant_id=participant.id,
            content="hello with meta",
            metadata={"room_query": {"target_room_id": "xyz", "source_room_id": "abc"}},
        )

        # Pre-seed a message WITHOUT metadata
        msg_without_meta = await append_message(
            db,
            room_id=room.id,
            participant_id=participant.id,
            content="hello no meta",
            metadata=None,
        )

        await db.commit()

        token = create_user_token(user.id, user.email, False, secret=config.jwt_secret)

        app = create_app(config)
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
        }

    await engine.dispose()


class TestRestMessageMetadataAlias:
    """Issue #61 — REST MessageOut must serialize extra_metadata as 'metadata'."""

    @pytest.mark.asyncio
    async def test_rest_response_uses_metadata_key(self, msg_env) -> None:
        """GET /rooms/{id}/messages should return 'metadata', not 'extra_metadata'."""
        app = msg_env["app"]
        token = msg_env["token"]
        room_id = msg_env["room"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.get(
                f"/api/v1/rooms/{room_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) >= 1

        # Find the message with metadata
        msg_with = next(m for m in messages if m["content"] == "hello with meta")
        assert "metadata" in msg_with, "REST response should contain 'metadata' key"
        assert "extra_metadata" not in msg_with, "REST response should NOT contain 'extra_metadata' key"
        assert msg_with["metadata"]["room_query"]["target_room_id"] == "xyz"

    @pytest.mark.asyncio
    async def test_rest_response_metadata_null_when_absent(self, msg_env) -> None:
        """Messages without metadata should have metadata: null."""
        app = msg_env["app"]
        token = msg_env["token"]
        room_id = msg_env["room"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.get(
                f"/api/v1/rooms/{room_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        messages = resp.json()

        msg_without = next(m for m in messages if m["content"] == "hello no meta")
        assert "metadata" in msg_without
        assert msg_without["metadata"] is None
        assert "extra_metadata" not in msg_without


class TestRestMessageCreatedAtTimezone:
    """Issue #93 — ``created_at`` must carry a timezone designator.

    Without a designator, ECMAScript parses the ISO string as local
    time, shifting KST clients nine hours into the past and breaking
    the ``RoomQueryBanner`` TTL filter.
    """

    TZ_SUFFIX = re.compile(r"(Z|[+\-]\d{2}:?\d{2})$")

    @pytest.mark.asyncio
    async def test_created_at_has_timezone_designator(self, msg_env) -> None:
        app = msg_env["app"]
        token = msg_env["token"]
        room_id = msg_env["room"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.get(
                f"/api/v1/rooms/{room_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) >= 1
        for m in messages:
            assert self.TZ_SUFFIX.search(m["created_at"]), (
                f"created_at missing timezone designator: {m['created_at']!r}"
            )
