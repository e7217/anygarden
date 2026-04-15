"""Tests for ``/api/v1/rooms/{room_id}/invites`` and
``/api/v1/invites/{invite_id}``.

PR B of the anonymous-guest RFC (#22). Token acceptance lives in
PR C; these tests only exercise the admin-side lifecycle: create,
list, revoke, auth gates, and abuse guards.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from doorae.api.v1.invites import (
    _MAX_ACTIVE_INVITES_PER_ROOM,
    _CREATE_RATE_LIMIT_MAX_PER_WINDOW,
    _reset_create_rate_limit,
)
from doorae.app import create_app
from doorae.auth.invite_token import verify_invite_token
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Base, Participant, Project, Room, RoomInviteLink, User
from sqlalchemy import select


# -- Fixtures ----------------------------------------------------------------


@pytest_asyncio.fixture()
async def env(config: DooraeSettings):
    """Two rooms with distinct admin/owner users for cross-room tests."""
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        owner = User(email="owner@doorae.io", password_hash="x")
        member = User(email="member@doorae.io", password_hash="x")
        outsider = User(email="outsider@doorae.io", password_hash="x")
        admin = User(email="admin@doorae.io", password_hash="x", is_admin=True)
        db.add_all([owner, member, outsider, admin])
        await db.flush()

        project = Project(name="proj")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="room-a")
        other_room = Room(project_id=project.id, name="room-b")
        db.add_all([room, other_room])
        await db.flush()

        db.add(Participant(room_id=room.id, user_id=owner.id, role="owner"))
        db.add(Participant(room_id=room.id, user_id=member.id, role="member"))
        # outsider is deliberately not a participant of any room.
        await db.commit()
        for obj in (owner, member, outsider, admin, project, room, other_room):
            await db.refresh(obj)

    owner_token = create_user_token(owner.id, owner.email, False, secret=config.jwt_secret)
    member_token = create_user_token(member.id, member.email, False, secret=config.jwt_secret)
    outsider_token = create_user_token(
        outsider.id, outsider.email, False, secret=config.jwt_secret
    )
    admin_token = create_user_token(admin.id, admin.email, True, secret=config.jwt_secret)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = session_factory

    _reset_create_rate_limit()

    yield {
        "app": app,
        "session_factory": session_factory,
        "owner": owner,
        "member": member,
        "outsider": outsider,
        "admin": admin,
        "room": room,
        "other_room": other_room,
        "owner_token": owner_token,
        "member_token": member_token,
        "outsider_token": outsider_token,
        "admin_token": admin_token,
    }

    _reset_create_rate_limit()
    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- Create ------------------------------------------------------------------


class TestCreateInvite:
    @pytest.mark.asyncio
    async def test_owner_can_create(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={"expires_in_seconds": 3600, "max_uses": 5},
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["room_id"] == env["room"].id
            # Token shown exactly once at creation, prefixed and usable
            # for hash verification (plaintext survives long enough for
            # the client to store it).
            token = data["token"]
            assert token.startswith("inv_")
            assert data["max_uses"] == 5
            assert data["use_count"] == 0

        # Token hash matches the stored row. We intentionally look up
        # by lookup_hint to prove the full inv_<hint><body> pattern.
        async with env["session_factory"]() as db:
            rows = (
                await db.execute(
                    select(RoomInviteLink).where(
                        RoomInviteLink.lookup_hint == token[:12]
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
            assert verify_invite_token(token, rows[0].token_hash)
            # Plaintext MUST NOT be stored
            assert rows[0].token_hash != token

    @pytest.mark.asyncio
    async def test_global_admin_can_create_without_membership(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={},
                headers=_auth(env["admin_token"]),
            )
            assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_member_role_cannot_create(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={},
                headers=_auth(env["member_token"]),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_outsider_cannot_create(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={},
                headers=_auth(env["outsider_token"]),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_room_returns_404_for_global_admin(self, env) -> None:
        """Global admins pass authz before the existence check, so
        they see the real 404. Non-admin non-members get a
        room-existence-hiding 403 instead — see the next test."""
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/rooms/nonexistent/invites",
                json={},
                headers=_auth(env["admin_token"]),
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_room_hidden_from_non_admin(self, env) -> None:
        """Authz runs before existence — non-members never learn
        whether a room exists via this endpoint."""
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/rooms/nonexistent/invites",
                json={},
                headers=_auth(env["outsider_token"]),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_rate_limit_trips_after_threshold(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(_CREATE_RATE_LIMIT_MAX_PER_WINDOW):
                resp = await client.post(
                    f"/api/v1/rooms/{env['room'].id}/invites",
                    json={},
                    headers=_auth(env["owner_token"]),
                )
                assert resp.status_code == 201
            # 11th within the window
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={},
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_active_invite_cap_blocks_overflow(self, env) -> None:
        # Seed the room with exactly the cap's worth of active invites
        # directly in the DB to avoid hitting the per-user rate limit.
        async with env["session_factory"]() as db:
            for _ in range(_MAX_ACTIVE_INVITES_PER_ROOM):
                db.add(
                    RoomInviteLink(
                        room_id=env["room"].id,
                        created_by_user_id=env["owner"].id,
                        token_hash="hash",
                        lookup_hint="inv_aaaaaaaa",
                    )
                )
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={},
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_expired_invites_do_not_count_against_cap(self, env) -> None:
        """Expired invites drop out of the active-cap predicate."""
        from datetime import datetime, timedelta, timezone

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        async with env["session_factory"]() as db:
            for _ in range(_MAX_ACTIVE_INVITES_PER_ROOM):
                db.add(
                    RoomInviteLink(
                        room_id=env["room"].id,
                        created_by_user_id=env["owner"].id,
                        token_hash="hash",
                        lookup_hint="inv_aaaaaaaa",
                        expires_at=past,
                    )
                )
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={},
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_max_uses_exhausted_do_not_count_against_cap(self, env) -> None:
        """Invites whose ``use_count >= max_uses`` are also inactive."""
        async with env["session_factory"]() as db:
            for _ in range(_MAX_ACTIVE_INVITES_PER_ROOM):
                db.add(
                    RoomInviteLink(
                        room_id=env["room"].id,
                        created_by_user_id=env["owner"].id,
                        token_hash="hash",
                        lookup_hint="inv_aaaaaaaa",
                        max_uses=1,
                        use_count=1,
                    )
                )
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={},
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_revoked_invites_do_not_count_against_cap(self, env) -> None:
        """Revoked invites are inactive and must free up cap slots."""
        from datetime import datetime, timezone

        async with env["session_factory"]() as db:
            for _ in range(_MAX_ACTIVE_INVITES_PER_ROOM):
                db.add(
                    RoomInviteLink(
                        room_id=env["room"].id,
                        created_by_user_id=env["owner"].id,
                        token_hash="hash",
                        lookup_hint="inv_aaaaaaaa",
                        revoked_at=datetime.now(timezone.utc),
                    )
                )
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{env['room'].id}/invites",
                json={},
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 201


# -- List --------------------------------------------------------------------


class TestListInvites:
    @pytest.mark.asyncio
    async def test_owner_sees_room_invites_only(self, env) -> None:
        async with env["session_factory"]() as db:
            db.add(
                RoomInviteLink(
                    room_id=env["room"].id,
                    created_by_user_id=env["owner"].id,
                    token_hash="h1",
                    lookup_hint="inv_aaaaaaaa",
                )
            )
            db.add(
                RoomInviteLink(
                    room_id=env["other_room"].id,
                    created_by_user_id=env["admin"].id,
                    token_hash="h2",
                    lookup_hint="inv_bbbbbbbb",
                )
            )
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms/{env['room'].id}/invites",
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["room_id"] == env["room"].id
            # Response does NOT leak token hashes.
            assert "token_hash" not in data[0]
            assert "token" not in data[0]

    @pytest.mark.asyncio
    async def test_excludes_revoked_by_default(self, env) -> None:
        from datetime import datetime, timezone

        async with env["session_factory"]() as db:
            db.add(
                RoomInviteLink(
                    room_id=env["room"].id,
                    created_by_user_id=env["owner"].id,
                    token_hash="h",
                    lookup_hint="inv_aaaaaaaa",
                    revoked_at=datetime.now(timezone.utc),
                )
            )
            db.add(
                RoomInviteLink(
                    room_id=env["room"].id,
                    created_by_user_id=env["owner"].id,
                    token_hash="h2",
                    lookup_hint="inv_bbbbbbbb",
                )
            )
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms/{env['room'].id}/invites",
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 200
            assert len(resp.json()) == 1

            resp = await client.get(
                f"/api/v1/rooms/{env['room'].id}/invites?include_revoked=true",
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 200
            assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_member_cannot_list(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms/{env['room'].id}/invites",
                headers=_auth(env["member_token"]),
            )
            assert resp.status_code == 403


# -- Revoke ------------------------------------------------------------------


class TestRevokeInvite:
    @pytest.mark.asyncio
    async def test_owner_can_revoke(self, env) -> None:
        async with env["session_factory"]() as db:
            invite = RoomInviteLink(
                room_id=env["room"].id,
                created_by_user_id=env["owner"].id,
                token_hash="h",
                lookup_hint="inv_aaaaaaaa",
            )
            db.add(invite)
            await db.commit()
            await db.refresh(invite)
            invite_id = invite.id

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/invites/{invite_id}",
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 204

        async with env["session_factory"]() as db:
            row = (
                await db.execute(
                    select(RoomInviteLink).where(RoomInviteLink.id == invite_id)
                )
            ).scalar_one()
            assert row.revoked_at is not None

    @pytest.mark.asyncio
    async def test_revoke_is_idempotent(self, env) -> None:
        from datetime import datetime, timezone

        async with env["session_factory"]() as db:
            invite = RoomInviteLink(
                room_id=env["room"].id,
                created_by_user_id=env["owner"].id,
                token_hash="h",
                lookup_hint="inv_aaaaaaaa",
                revoked_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
            db.add(invite)
            await db.commit()
            await db.refresh(invite)

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/invites/{invite.id}",
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 204

        async with env["session_factory"]() as db:
            row = (
                await db.execute(
                    select(RoomInviteLink).where(RoomInviteLink.id == invite.id)
                )
            ).scalar_one()
            # Original revoked_at timestamp preserved — we don't overwrite.
            assert row.revoked_at.year == 2020

    @pytest.mark.asyncio
    async def test_cross_room_admin_cannot_revoke(self, env) -> None:
        """Owner of room A cannot revoke invites on room B."""
        async with env["session_factory"]() as db:
            invite = RoomInviteLink(
                room_id=env["other_room"].id,
                created_by_user_id=env["admin"].id,
                token_hash="h",
                lookup_hint="inv_aaaaaaaa",
            )
            db.add(invite)
            await db.commit()
            await db.refresh(invite)

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/invites/{invite.id}",
                headers=_auth(env["owner_token"]),
            )
            # owner has no participant row in other_room
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_invite_404(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                "/api/v1/invites/does-not-exist",
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 404
