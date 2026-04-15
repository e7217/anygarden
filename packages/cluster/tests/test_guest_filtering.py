"""Tests for guest list/read filtering — PR E of RFC #22.

After PR C gated *mutations* and PR D gated the WS send path,
this PR trims the *read* surface:
- ``GET /rooms`` returns at most the guest's single bound room.
- ``GET /rooms/{id}`` rejects cross-room reads with 403.
- ``GET /rooms/{id}/sub-rooms`` is closed to guests entirely.
- ``GET /projects`` is closed to guests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from doorae.app import create_app
from doorae.auth.invite_token import hash_invite_token
from doorae.auth.jwt import create_guest_token, create_user_token
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


# ── Fixtures ────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def env(config: DooraeSettings) -> AsyncIterator[dict]:
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        owner = User(email="own@doorae.io", password_hash="x")
        db.add(owner)
        await db.flush()
        project = Project(name="proj")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="main")
        other_room = Room(project_id=project.id, name="other")
        db.add_all([room, other_room])
        await db.flush()
        # Create sub_room AFTER its parent has flushed so the FK
        # target id is guaranteed to exist in SQLite's current txn.
        sub_room = Room(
            project_id=project.id, name="sub", parent_room_id=room.id
        )
        db.add(sub_room)
        await db.flush()
        db.add(Participant(room_id=room.id, user_id=owner.id, role="owner"))

        token_plain = "inv_" + "z" * 40
        token_hash, hint = hash_invite_token(token_plain)
        invite = RoomInviteLink(
            room_id=room.id,
            created_by_user_id=owner.id,
            token_hash=token_hash,
            lookup_hint=hint,
        )
        db.add(invite)

        # Guest User + Participant directly, so tests don't depend
        # on the /auth/guest endpoint.
        guest_user = User(
            email=None,
            password_hash=None,
            is_anonymous=True,
            display_name="G",
        )
        db.add(guest_user)
        await db.flush()
        db.add(
            Participant(
                room_id=room.id, user_id=guest_user.id, role="member"
            )
        )
        await db.commit()

        for obj in (owner, project, room, other_room, sub_room, guest_user, invite):
            await db.refresh(obj)

    owner_token = create_user_token(
        owner.id, owner.email, False, secret=config.jwt_secret
    )
    guest_jwt = create_guest_token(
        user_id=guest_user.id,
        room_id=room.id,
        invite_id=invite.id,
        display_name="G",
        secret=config.jwt_secret,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = session_factory

    yield {
        "app": app,
        "session_factory": session_factory,
        "owner": owner,
        "project": project,
        "room": room,
        "other_room": other_room,
        "sub_room": sub_room,
        "guest_user": guest_user,
        "owner_token": owner_token,
        "guest_jwt": guest_jwt,
    }

    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── /rooms list ─────────────────────────────────────────────────────


class TestListRoomsGuestScope:
    @pytest.mark.asyncio
    async def test_guest_sees_only_bound_room(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/rooms",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 200
            ids = [r["id"] for r in resp.json()]
            assert ids == [env["room"].id]

    @pytest.mark.asyncio
    async def test_guest_cannot_widen_with_project_filter(self, env) -> None:
        """A spoofed ``project_id`` must not leak sibling rooms."""
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms?project_id={env['project'].id}",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 200
            ids = [r["id"] for r in resp.json()]
            assert ids == [env["room"].id]  # only bound room, not other_room

    @pytest.mark.asyncio
    async def test_guest_cannot_widen_with_is_dm_filter(self, env) -> None:
        """``is_dm`` is a post-filter; the guest room_id pin must still
        apply. A guest bound to a non-DM room requesting ``is_dm=true``
        returns an empty list instead of leaking every DM room in the
        system.
        """
        # Seed a DM room that does NOT include the guest so a buggy
        # filter would surface it.
        async with env["session_factory"]() as db:
            dm = Room(
                project_id=env["project"].id, name="secret-dm", is_dm=True
            )
            db.add(dm)
            await db.commit()

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # is_dm=true: the guest's bound room is NOT a DM, so the
            # combined filter returns an empty list.
            resp = await client.get(
                "/api/v1/rooms?is_dm=true",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 200
            assert resp.json() == []

            # is_dm=false: still only the one bound room.
            resp = await client.get(
                "/api/v1/rooms?is_dm=false",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 200
            ids = [r["id"] for r in resp.json()]
            assert ids == [env["room"].id]

    @pytest.mark.asyncio
    async def test_guest_project_id_from_different_project(self, env) -> None:
        """A guest supplying an unrelated project_id must still only
        ever see their bound room — not leak cross-project rooms."""
        async with env["session_factory"]() as db:
            # Build a completely separate project + room. The guest's
            # JWT is NOT bound to any room in this project.
            other_project = Project(name="outsider-project")
            db.add(other_project)
            await db.flush()
            outsider = Room(project_id=other_project.id, name="outsider")
            db.add(outsider)
            await db.commit()
            await db.refresh(other_project)

        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms?project_id={other_project.id}",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 200
            # Cross-project AND room_id pin ⇒ empty set.
            assert resp.json() == []

    @pytest.mark.asyncio
    async def test_user_still_sees_everything(self, env) -> None:
        """Regression guard: the guest branch must not affect the
        registered-user code path."""
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/rooms",
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 200
            ids = {r["id"] for r in resp.json()}
            # Owner sees main, other, and the sub-room.
            assert ids >= {env["room"].id, env["other_room"].id, env["sub_room"].id}


# ── /rooms/{id} detail ──────────────────────────────────────────────


class TestGetRoomGuestScope:
    @pytest.mark.asyncio
    async def test_guest_own_room_ok(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms/{env['room'].id}",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 200
            assert resp.json()["id"] == env["room"].id

    @pytest.mark.asyncio
    async def test_guest_other_room_rejected(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms/{env['other_room'].id}",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_unknown_room_id_hides_existence(self, env) -> None:
        """The 403 must beat the 404 so probing a random UUID
        can't reveal whether it exists."""
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/rooms/does-not-exist",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 403


# ── /rooms/{id}/sub-rooms ───────────────────────────────────────────


class TestSubRoomListGuest:
    @pytest.mark.asyncio
    async def test_guest_forbidden(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms/{env['room'].id}/sub-rooms",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_user_still_allowed(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms/{env['room'].id}/sub-rooms",
                headers=_auth(env["owner_token"]),
            )
            assert resp.status_code == 200
            ids = [r["id"] for r in resp.json()]
            assert env["sub_room"].id in ids


# ── /projects ───────────────────────────────────────────────────────


class TestProjectsGuest:
    @pytest.mark.asyncio
    async def test_guest_list_projects_forbidden(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/projects",
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_create_project_forbidden(self, env) -> None:
        transport = ASGITransport(app=env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/projects",
                json={"name": "x"},
                headers=_auth(env["guest_jwt"]),
            )
            assert resp.status_code == 403
