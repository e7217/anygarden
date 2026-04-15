"""Tests for guest JWT issuance (``POST /api/v1/auth/guest``) and the
``Identity.kind == "guest"`` resolver path.

PR C of the anonymous-guest RFC (#22). Also covers ``forbid_guest``
blocking guest tokens on mutation endpoints and the
``require_room_member`` branch that cross-checks the JWT's
``room_id`` claim against the target room.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from doorae.app import create_app
from doorae.auth.dependencies import Identity, get_identity
from doorae.auth.invite_token import hash_invite_token
from doorae.auth.jwt import (
    GuestClaims,
    UserClaims,
    create_guest_token,
    create_user_token,
    decode_any_user_token,
    verify_guest_token,
    verify_user_token,
    InvalidToken,
)
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Base,
    Participant,
    Project,
    Room,
    RoomInviteLink,
    User,
)


# -- JWT unit tests ----------------------------------------------------------


class TestGuestJWTCodec:
    def test_round_trip_guest(self) -> None:
        secret = "s"
        t = create_guest_token(
            user_id="u",
            room_id="r",
            invite_id="i",
            display_name="Alice",
            secret=secret,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        claims = verify_guest_token(t, secret=secret)
        assert isinstance(claims, GuestClaims)
        assert claims.user_id == "u"
        assert claims.room_id == "r"
        assert claims.invite_id == "i"
        assert claims.display_name == "Alice"

    def test_user_token_rejected_by_guest_verifier(self) -> None:
        secret = "s"
        t = create_user_token("u", "a@x", False, secret=secret)
        with pytest.raises(InvalidToken):
            verify_guest_token(t, secret=secret)

    def test_guest_token_rejected_by_user_verifier(self) -> None:
        secret = "s"
        t = create_guest_token(
            user_id="u",
            room_id="r",
            invite_id="i",
            display_name="A",
            secret=secret,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        with pytest.raises(InvalidToken):
            verify_user_token(t, secret=secret)

    def test_decode_any_dispatches_by_is_guest(self) -> None:
        secret = "s"
        user_t = create_user_token("u", "a@x", True, secret=secret)
        guest_t = create_guest_token(
            user_id="g",
            room_id="r",
            invite_id="i",
            display_name="G",
            secret=secret,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        assert isinstance(decode_any_user_token(user_t, secret=secret), UserClaims)
        assert isinstance(decode_any_user_token(guest_t, secret=secret), GuestClaims)


# -- End-to-end fixtures -----------------------------------------------------


@pytest_asyncio.fixture()
async def env(config: DooraeSettings) -> AsyncIterator[dict]:
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        owner = User(email="owner@doorae.io", password_hash="x")
        db.add(owner)
        await db.flush()
        project = Project(name="proj")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="main")
        other_room = Room(project_id=project.id, name="other")
        db.add_all([room, other_room])
        await db.flush()
        db.add(Participant(room_id=room.id, user_id=owner.id, role="owner"))

        token_plain = "inv_" + "a" * 40
        token_hash, hint = hash_invite_token(token_plain)
        invite = RoomInviteLink(
            room_id=room.id,
            created_by_user_id=owner.id,
            token_hash=token_hash,
            lookup_hint=hint,
            max_uses=3,
        )
        db.add(invite)
        await db.commit()
        for obj in (owner, project, room, other_room, invite):
            await db.refresh(obj)

    owner_token = create_user_token(
        owner.id, owner.email, False, secret=config.jwt_secret
    )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = session_factory

    yield {
        "app": app,
        "session_factory": session_factory,
        "config": config,
        "owner": owner,
        "room": room,
        "other_room": other_room,
        "invite": invite,
        "invite_token": token_plain,
        "owner_token": owner_token,
    }

    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- POST /api/v1/auth/guest -------------------------------------------------


class TestAcceptGuestInvite:
    @pytest.mark.asyncio
    async def test_accepts_valid_invite(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "Alice"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["room_id"] == env["room"].id
            assert data["display_name"] == "Alice"
            assert data["token"]  # issued

        # A new guest User row and Participant row must exist; the
        # invite's use_count incremented.
        async with env["session_factory"]() as db:
            guests = (
                await db.execute(select(User).where(User.is_anonymous.is_(True)))
            ).scalars().all()
            assert len(guests) == 1
            assert guests[0].display_name == "Alice"
            assert guests[0].email is None

            parts = (
                await db.execute(
                    select(Participant).where(Participant.user_id == guests[0].id)
                )
            ).scalars().all()
            assert len(parts) == 1
            assert parts[0].room_id == env["room"].id

            updated_invite = (
                await db.execute(
                    select(RoomInviteLink).where(RoomInviteLink.id == env["invite"].id)
                )
            ).scalar_one()
            assert updated_invite.use_count == 1

    @pytest.mark.asyncio
    async def test_bad_token_401(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/guest",
                json={"token": "inv_" + "b" * 40, "display_name": "X"},
            )
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_revoked_invite_rejected(self, env) -> None:
        async with env["session_factory"]() as db:
            inv = (
                await db.execute(
                    select(RoomInviteLink).where(RoomInviteLink.id == env["invite"].id)
                )
            ).scalar_one()
            inv.revoked_at = datetime.now(timezone.utc)
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "X"},
            )
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_invite_rejected(self, env) -> None:
        async with env["session_factory"]() as db:
            inv = (
                await db.execute(
                    select(RoomInviteLink).where(RoomInviteLink.id == env["invite"].id)
                )
            ).scalar_one()
            inv.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "X"},
            )
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_max_uses_exhausted_rejected(self, env) -> None:
        async with env["session_factory"]() as db:
            inv = (
                await db.execute(
                    select(RoomInviteLink).where(RoomInviteLink.id == env["invite"].id)
                )
            ).scalar_one()
            inv.use_count = inv.max_uses
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "X"},
            )
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_issued_token_resolves_to_guest_identity(self, env) -> None:
        """The returned JWT must round-trip through ``get_identity`` as
        ``kind=\"guest\"`` with the right ``room_id``."""
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "Bob"},
            )
            assert resp.status_code == 201
            jwt_token = resp.json()["token"]

        async with env["session_factory"]() as db:
            identity: Identity = await get_identity(
                db,
                jwt_secret=env["config"].jwt_secret,
                authorization=f"Bearer {jwt_token}",
            )
            assert identity.kind == "guest"
            assert isinstance(identity.claims, GuestClaims)
            assert identity.claims.room_id == env["room"].id


# -- forbid_guest gate -------------------------------------------------------


class TestForbidGuestGate:
    @pytest.mark.asyncio
    async def test_guest_cannot_create_room(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            accept = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "G"},
            )
            jwt_token = accept.json()["token"]

            resp = await client.post(
                "/api/v1/rooms",
                json={"project_id": "p", "name": "new"},
                headers=_auth(jwt_token),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_cannot_add_participant(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            accept = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "G"},
            )
            jwt_token = accept.json()["token"]

            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/participants",
                json={"user_id": env["owner"].id},
                headers=_auth(jwt_token),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_cannot_create_invite(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            accept = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "G"},
            )
            jwt_token = accept.json()["token"]

            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={},
                headers=_auth(jwt_token),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_cannot_list_saved_messages(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            accept = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "G"},
            )
            jwt_token = accept.json()["token"]

            resp = await client.get(
                "/api/v1/saved",
                headers=_auth(jwt_token),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_cannot_list_machines(self, env) -> None:
        """Regression: ``list_machines`` used to AttributeError on
        guest claims (no is_admin field). Must now cleanly 403."""
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            accept = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "G"},
            )
            jwt_token = accept.json()["token"]

            resp = await client.get("/api/v1/machines", headers=_auth(jwt_token))
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_cannot_read_other_room_messages(self, env) -> None:
        """Guest JWT is bound to one room. ``GET /rooms/{id}/messages``
        for a different room must 403 even though the guest's JWT
        verifies."""
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            accept = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "G"},
            )
            jwt_token = accept.json()["token"]

            # Own room: 200 (empty history is fine)
            resp = await client.get(
                f"/api/v1/rooms/{env['room'].id}/messages",
                headers=_auth(jwt_token),
            )
            assert resp.status_code == 200

            # Cross-room: 403
            resp = await client.get(
                f"/api/v1/rooms/{env['other_room'].id}/messages",
                headers=_auth(jwt_token),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_accept_broadcasts_to_existing_room_members(self, env) -> None:
        """RoomMembershipChangedOut must reach existing room members'
        WS connections so host UIs refresh — regression guard for a
        bug where the query filtered on the guest's user_id and
        always matched nothing."""
        import json

        from doorae.ws.manager import ConnectionManager

        # Set up the connection manager (production initializes this
        # in the lifespan, which tests don't run).
        if not getattr(env["app"].state, "connection_manager", None):
            env["app"].state.connection_manager = ConnectionManager()
        manager = env["app"].state.connection_manager

        received: list[str] = []

        class FakeWS:
            async def send_text(self, data: str) -> None:
                received.append(data)

        # Subscribe the room owner's Participant so they appear as an
        # existing member the broadcast can target.
        async with env["session_factory"]() as db:
            owner_pid = (
                await db.execute(
                    select(Participant.id).where(
                        Participant.room_id == env["room"].id,
                        Participant.user_id == env["owner"].id,
                    )
                )
            ).scalar_one()

        ws = FakeWS()
        await manager.subscribe(env["room"].id, owner_pid, ws)  # type: ignore[arg-type]

        try:
            transport = ASGITransport(app=env["app"])
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/guest",
                    json={"token": env["invite_token"], "display_name": "Bob"},
                )
                assert resp.status_code == 201
        finally:
            await manager.unsubscribe(owner_pid)

        frames = [
            json.loads(raw)
            for raw in received
            if json.loads(raw).get("type") == "room_membership_changed"
        ]
        assert len(frames) == 1
        assert frames[0]["action"] == "added"
        assert frames[0]["room_id"] == env["room"].id

    @pytest.mark.asyncio
    async def test_guest_cannot_hit_me(self, env) -> None:
        """``GET /me`` assumes registered claims and would KeyError on
        email — ``forbid_guest`` stops it cleanly."""
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            accept = await client.post(
                "/api/v1/auth/guest",
                json={"token": env["invite_token"], "display_name": "G"},
            )
            jwt_token = accept.json()["token"]

            resp = await client.get("/api/v1/auth/me", headers=_auth(jwt_token))
            assert resp.status_code == 403


# -- require_room_member guest branch ---------------------------------------


class TestRequireRoomMemberGuestBranch:
    @pytest.mark.asyncio
    async def test_guest_cross_room_claim_rejected(self, env) -> None:
        """A guest JWT forged to claim the wrong room must 403 even if
        a Participant row happened to exist there."""
        from doorae.auth.dependencies import require_room_member
        from doorae.auth.jwt import GuestClaims
        from fastapi import HTTPException

        async with env["session_factory"]() as db:
            guest_user = User(
                email=None,
                password_hash=None,
                is_anonymous=True,
                display_name="X",
            )
            db.add(guest_user)
            await db.flush()
            # Seed a Participant in ``other_room`` to simulate DB
            # tampering — the claim mismatch must still block access.
            db.add(
                Participant(
                    room_id=env["other_room"].id,
                    user_id=guest_user.id,
                    role="member",
                )
            )
            await db.commit()
            await db.refresh(guest_user)

            bogus_identity = Identity(
                kind="guest",
                id=guest_user.id,
                claims=GuestClaims(
                    user_id=guest_user.id,
                    room_id=env["room"].id,  # pretend bound to ``room``
                    invite_id="fake",
                    display_name="X",
                ),
            )
            with pytest.raises(HTTPException) as excinfo:
                await require_room_member(
                    env["other_room"].id,
                    bogus_identity,
                    db,
                )
            assert excinfo.value.status_code == 403
