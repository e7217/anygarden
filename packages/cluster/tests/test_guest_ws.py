"""WS-level guest handling — PR D of RFC #22.

Covers:
- ``GuestRoomAggregateLimiter`` bucket semantics.
- Guest SendFrame path strips ``#room:`` mentions silently.
- Guest per-participant cooldown uses the stricter bucket.
- Guest room-aggregate rate limit kicks in on too many mentions.
- Representative auto-join is never triggered by a guest.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest_asyncio
from sqlalchemy import select
from starlette.testclient import TestClient

from doorae.app import create_app
from doorae.auth.invite_token import hash_invite_token
from doorae.auth.jwt import create_guest_token, create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent,
    Base,
    Participant,
    Project,
    Room,
    RoomInviteLink,
    User,
)
from doorae.orchestration.rules import GuestRoomAggregateLimiter
from datetime import datetime, timedelta, timezone


# ── Unit tests for GuestRoomAggregateLimiter ─────────────────────────


class TestGuestRoomAggregateLimiter:
    def test_capacity_bursts_then_refuses(self) -> None:
        """A fresh room starts at full capacity and refuses once drained."""
        lim = GuestRoomAggregateLimiter(capacity=3, window_seconds=60.0)
        assert lim.check("r") is True
        assert lim.check("r") is True
        assert lim.check("r") is True
        assert lim.check("r") is False

    def test_rooms_are_independent(self) -> None:
        """Exhausting room A must not affect room B."""
        lim = GuestRoomAggregateLimiter(capacity=1, window_seconds=60.0)
        assert lim.check("a") is True
        assert lim.check("a") is False
        assert lim.check("b") is True


# ── End-to-end fixtures ──────────────────────────────────────────────


@pytest_asyncio.fixture()
async def guest_env(config: DooraeSettings) -> AsyncIterator[dict]:
    """Seed a room, an agent participant (potential representative), an
    invite, and issue a guest JWT ready for WS use.
    """
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        owner = User(email="own@doorae.io", password_hash="x")
        db.add(owner)
        await db.flush()
        project = Project(name="p")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="main")
        other_room = Room(
            project_id=project.id, name="other", representative_agent_id=None
        )
        db.add_all([room, other_room])
        await db.flush()
        owner_part = Participant(room_id=room.id, user_id=owner.id, role="owner")
        db.add(owner_part)

        # Agent participant in ``room`` — guest should be able to
        # mention them as @user:<id>.
        agent = Agent(name="Helper", engine="anthropic", actual_state="running")
        db.add(agent)
        await db.flush()
        agent_part = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add(agent_part)

        # Representative for ``other_room`` — used to verify guest
        # never auto-joins it via #room mention.
        other_room.representative_agent_id = agent.id

        # Create a guest session via the invite API directly.
        token_plain = "inv_" + "z" * 40
        token_hash, hint = hash_invite_token(token_plain)
        invite = RoomInviteLink(
            room_id=room.id,
            created_by_user_id=owner.id,
            token_hash=token_hash,
            lookup_hint=hint,
        )
        db.add(invite)

        await db.commit()
        for obj in (owner, owner_part, agent, agent_part, room, other_room, invite):
            await db.refresh(obj)

        # Create a guest user + participant directly so the test does
        # not depend on the /auth/guest endpoint's side effects.
        guest_user = User(
            email=None,
            password_hash=None,
            is_anonymous=True,
            display_name="Guest",
        )
        db.add(guest_user)
        await db.flush()
        guest_part = Participant(
            room_id=room.id, user_id=guest_user.id, role="member"
        )
        db.add(guest_part)
        await db.commit()
        await db.refresh(guest_user)
        await db.refresh(guest_part)

    owner_token = create_user_token(
        owner.id, owner.email, False, secret=config.jwt_secret
    )
    guest_jwt = create_guest_token(
        user_id=guest_user.id,
        room_id=room.id,
        invite_id=invite.id,
        display_name="Guest",
        secret=config.jwt_secret,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = session_factory

    yield {
        "app": app,
        "session_factory": session_factory,
        "room": room,
        "other_room": other_room,
        "agent": agent,
        "agent_part": agent_part,
        "guest_user": guest_user,
        "guest_part": guest_part,
        "guest_jwt": guest_jwt,
        "owner_token": owner_token,
    }

    await engine.dispose()


# ── WS-level tests ───────────────────────────────────────────────────


class TestGuestWSSendFrame:
    def test_guest_cannot_trigger_room_mention_routing(self, guest_env) -> None:
        """A guest's ``<#room:X>`` mention is silently stripped, so the
        representative auto-join path never runs and no Participant
        row is added to the target room.
        """
        app = guest_env["app"]
        room_id = guest_env["room"].id
        other_room_id = guest_env["other_room"].id
        agent_id = guest_env["agent"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["doorae.v1", f"bearer.{guest_env['guest_jwt']}"],
            ) as ws:
                welcome = json.loads(ws.receive_text())
                assert welcome["type"] == "welcome"
                ws.send_text(
                    json.dumps(
                        {
                            "type": "send",
                            "content": f"ping <#room:{other_room_id}>",
                        }
                    )
                )
                out = json.loads(ws.receive_text())
                assert out["type"] == "message"
                # The #room mention must not survive into metadata.
                mentions = (out.get("metadata") or {}).get("mentions") or []
                assert all(m.get("type") != "room" for m in mentions)
                # No room_query was attached.
                assert "room_query" not in (out.get("metadata") or {})

        # The representative agent MUST NOT have been added to the
        # target room as a Participant.
        async def _check() -> int:
            async with guest_env["session_factory"]() as db:
                rows = (
                    await db.execute(
                        select(Participant).where(
                            Participant.room_id == other_room_id,
                            Participant.agent_id == agent_id,
                        )
                    )
                ).scalars().all()
                return len(rows)

        import asyncio

        assert asyncio.run(_check()) == 0

    def test_guest_user_mention_survives(self, guest_env) -> None:
        """Guests may address an agent-as-user via ``<@user:id>`` —
        these mentions are preserved for downstream routing."""
        app = guest_env["app"]
        room_id = guest_env["room"].id
        agent_id = guest_env["agent"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["doorae.v1", f"bearer.{guest_env['guest_jwt']}"],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(
                    json.dumps(
                        {
                            "type": "send",
                            "content": f"hi <@user:{agent_id}>",
                        }
                    )
                )
                out = json.loads(ws.receive_text())
                assert out["type"] == "message"
                mentions = (out.get("metadata") or {}).get("mentions") or []
                assert any(
                    m.get("type") == "user" and m.get("id") == agent_id
                    for m in mentions
                )

    def test_guest_cooldown_stricter(self, guest_env) -> None:
        """Burst past the guest bucket (capacity=3) trips the
        cooldown error — and does so *before* a registered-user
        bucket (capacity=5) would. The error text is shared so we
        don't leak ``kind`` through the WS channel."""
        app = guest_env["app"]
        room_id = guest_env["room"].id

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["doorae.v1", f"bearer.{guest_env['guest_jwt']}"],
            ) as ws:
                ws.receive_text()  # welcome
                # 3 messages fit; 4th must trip.
                for i in range(3):
                    ws.send_text(json.dumps({"type": "send", "content": f"m{i}"}))
                    frame = json.loads(ws.receive_text())
                    assert frame["type"] == "message"

                ws.send_text(json.dumps({"type": "send", "content": "overflow"}))
                frame = json.loads(ws.receive_text())
                assert frame["type"] == "error"
                assert "rate limited" in frame["detail"].lower()
                # Must NOT leak guest vs user via the error string.
                assert "guest" not in frame["detail"].lower()

    def test_guest_room_aggregate_limit_triggers(self, guest_env) -> None:
        """Once the per-room guest bucket is drained, further guest
        *mention-bearing* sends return the aggregate-limit error."""
        app = guest_env["app"]
        room_id = guest_env["room"].id
        agent_id = guest_env["agent"].id

        with TestClient(app) as client:
            # Lifespan initialises the limiter — read it AFTER entering
            # the TestClient context.
            limiter: GuestRoomAggregateLimiter = app.state.guest_room_limiter
            for _ in range(20):
                limiter.check(room_id)
            assert limiter.check(room_id) is False

            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["doorae.v1", f"bearer.{guest_env['guest_jwt']}"],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(
                    json.dumps(
                        {
                            "type": "send",
                            "content": f"<@user:{agent_id}> help",
                        }
                    )
                )
                frame = json.loads(ws.receive_text())
                assert frame["type"] == "error"
                assert "aggregate" in frame["detail"].lower()
                # Matches the handler's generic error text — must
                # not leak the ``guest`` kind.
                assert "guest" not in frame["detail"].lower()

    def test_non_guest_unaffected_by_guest_room_limiter(self, guest_env) -> None:
        """Draining the guest room limiter must not rate-limit a
        registered user sending mentions in the same room."""
        app = guest_env["app"]
        room_id = guest_env["room"].id
        agent_id = guest_env["agent"].id
        owner_token = guest_env["owner_token"]

        with TestClient(app) as client:
            limiter: GuestRoomAggregateLimiter = app.state.guest_room_limiter
            for _ in range(20):
                limiter.check(room_id)

            with client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["doorae.v1", f"bearer.{owner_token}"],
            ) as ws:
                ws.receive_text()  # welcome
                ws.send_text(
                    json.dumps(
                        {"type": "send", "content": f"<@user:{agent_id}> hi"}
                    )
                )
                frame = json.loads(ws.receive_text())
                assert frame["type"] == "message"
