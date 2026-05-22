"""Tests for sidebar pin / reorder (#47).

Covers both the service layer (``set_room_pinned``,
``reorder_pinned_rooms``) and the REST endpoints exposed through
``/api/v1/rooms/{id}/pin`` and ``/api/v1/rooms/pin-order``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.rooms.service import (
    PIN_ORDER_STEP,
    reorder_pinned_rooms,
    set_room_pinned,
)


# -- Fixture ------------------------------------------------------------------


@pytest_asyncio.fixture()
async def pin_env(config: AnygardenSettings):
    """Seed a user with 3 rooms they participate in + 1 agent-only room.

    The agent-only room is there to make sure pin operations ignore
    participants that don't belong to the calling user.
    """
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    data: dict = {}
    async with session_factory() as db:
        user = User(email="pin-user@anygarden.io", password_hash="x")
        other = User(email="other@anygarden.io", password_hash="x")
        db.add_all([user, other])
        await db.flush()

        project = Project(name="pin-proj")
        db.add(project)
        await db.flush()

        rooms: list[Room] = []
        for name in ("alpha", "bravo", "charlie"):
            r = Room(project_id=project.id, name=name)
            db.add(r)
            await db.flush()
            db.add(Participant(room_id=r.id, user_id=user.id, role="member"))
            rooms.append(r)

        # Another user in "alpha" — pin ops must not touch their row.
        db.add(Participant(room_id=rooms[0].id, user_id=other.id, role="member"))

        # Agent-only room the user does NOT participate in.
        agent = Agent(name="bot", engine="claude_code")
        db.add(agent)
        await db.flush()
        agent_room = Room(project_id=project.id, name="agent-only")
        db.add(agent_room)
        await db.flush()
        db.add(Participant(room_id=agent_room.id, agent_id=agent.id, role="member"))

        await db.commit()
        for r in rooms:
            await db.refresh(r)
        await db.refresh(user)
        await db.refresh(other)
        await db.refresh(project)
        await db.refresh(agent_room)

        data = {
            "user": user,
            "other": other,
            "project": project,
            "rooms": rooms,
            "agent_room": agent_room,
            "session_factory": session_factory,
        }

    data["token"] = create_user_token(
        data["user"].id,
        data["user"].email,
        False,
        secret=config.jwt_secret,
    )
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = session_factory
    data["app"] = app

    yield data

    await engine.dispose()


# -- Service-level tests ------------------------------------------------------


class TestSetRoomPinned:
    @pytest.mark.asyncio
    async def test_pin_on_places_at_tail(self, pin_env) -> None:
        sf = pin_env["session_factory"]
        user = pin_env["user"]
        rooms = pin_env["rooms"]
        async with sf() as db:
            order = await set_room_pinned(
                db, user_id=user.id, room_id=rooms[0].id, pinned=True
            )
            await db.commit()
            assert order == [rooms[0].id]

            order = await set_room_pinned(
                db, user_id=user.id, room_id=rooms[2].id, pinned=True
            )
            await db.commit()
            assert order == [rooms[0].id, rooms[2].id]

            # Verify sort_order follows the sparse spacing.
            result = await db.execute(
                select(Participant).where(
                    Participant.user_id == user.id, Participant.pinned.is_(True)
                )
            )
            pins = sorted(
                result.scalars().all(), key=lambda p: p.sort_order or 0
            )
            assert pins[1].sort_order - pins[0].sort_order == PIN_ORDER_STEP

    @pytest.mark.asyncio
    async def test_pin_off_clears_sort_order(self, pin_env) -> None:
        sf = pin_env["session_factory"]
        user = pin_env["user"]
        rooms = pin_env["rooms"]
        async with sf() as db:
            await set_room_pinned(
                db, user_id=user.id, room_id=rooms[0].id, pinned=True
            )
            await db.commit()

            order = await set_room_pinned(
                db, user_id=user.id, room_id=rooms[0].id, pinned=False
            )
            await db.commit()
            assert order == []

            result = await db.execute(
                select(Participant).where(
                    Participant.user_id == user.id,
                    Participant.room_id == rooms[0].id,
                )
            )
            p = result.scalar_one()
            assert p.pinned is False
            assert p.sort_order is None

    @pytest.mark.asyncio
    async def test_pin_is_idempotent(self, pin_env) -> None:
        sf = pin_env["session_factory"]
        user = pin_env["user"]
        rooms = pin_env["rooms"]
        async with sf() as db:
            first = await set_room_pinned(
                db, user_id=user.id, room_id=rooms[0].id, pinned=True
            )
            await db.commit()
            second = await set_room_pinned(
                db, user_id=user.id, room_id=rooms[0].id, pinned=True
            )
            await db.commit()
            assert first == second == [rooms[0].id]

    @pytest.mark.asyncio
    async def test_pin_other_user_unaffected(self, pin_env) -> None:
        """Pinning for user A must not touch user B's Participant row."""
        sf = pin_env["session_factory"]
        user = pin_env["user"]
        other = pin_env["other"]
        rooms = pin_env["rooms"]
        async with sf() as db:
            await set_room_pinned(
                db, user_id=user.id, room_id=rooms[0].id, pinned=True
            )
            await db.commit()
            result = await db.execute(
                select(Participant).where(
                    Participant.user_id == other.id,
                    Participant.room_id == rooms[0].id,
                )
            )
            other_part = result.scalar_one()
            assert other_part.pinned is False
            assert other_part.sort_order is None

    @pytest.mark.asyncio
    async def test_pin_room_user_not_in_raises(self, pin_env) -> None:
        """Pinning a room the user doesn't participate in fails."""
        sf = pin_env["session_factory"]
        user = pin_env["user"]
        agent_room = pin_env["agent_room"]
        async with sf() as db:
            with pytest.raises(Exception):  # noqa: PT011 — HTTP or lookup error OK
                await set_room_pinned(
                    db, user_id=user.id, room_id=agent_room.id, pinned=True
                )


class TestReorderPinnedRooms:
    @pytest.mark.asyncio
    async def test_reorder_rewrites_sort_order(self, pin_env) -> None:
        sf = pin_env["session_factory"]
        user = pin_env["user"]
        rooms = pin_env["rooms"]
        async with sf() as db:
            for r in rooms:
                await set_room_pinned(
                    db, user_id=user.id, room_id=r.id, pinned=True
                )
            await db.commit()

            reversed_order = [rooms[2].id, rooms[1].id, rooms[0].id]
            result = await reorder_pinned_rooms(
                db, user_id=user.id, room_ids=reversed_order
            )
            await db.commit()
            assert result == reversed_order

            result = await db.execute(
                select(Participant).where(
                    Participant.user_id == user.id,
                    Participant.pinned.is_(True),
                )
            )
            pins = sorted(result.scalars().all(), key=lambda p: p.sort_order or 0)
            assert [p.room_id for p in pins] == reversed_order
            # Spacing preserved after rewrite.
            assert pins[1].sort_order - pins[0].sort_order == PIN_ORDER_STEP

    @pytest.mark.asyncio
    async def test_reorder_ignores_unpinned_rooms_in_snapshot(self, pin_env) -> None:
        """Snapshot including an unpinned room must not promote it."""
        sf = pin_env["session_factory"]
        user = pin_env["user"]
        rooms = pin_env["rooms"]
        async with sf() as db:
            await set_room_pinned(
                db, user_id=user.id, room_id=rooms[0].id, pinned=True
            )
            await db.commit()

            # rooms[1] is NOT pinned — should be silently ignored.
            out = await reorder_pinned_rooms(
                db, user_id=user.id, room_ids=[rooms[1].id, rooms[0].id]
            )
            await db.commit()
            assert out == [rooms[0].id]

            result = await db.execute(
                select(Participant).where(
                    Participant.user_id == user.id,
                    Participant.room_id == rooms[1].id,
                )
            )
            p = result.scalar_one()
            assert p.pinned is False

    @pytest.mark.asyncio
    async def test_reorder_empty_snapshot_is_noop(self, pin_env) -> None:
        sf = pin_env["session_factory"]
        user = pin_env["user"]
        rooms = pin_env["rooms"]
        async with sf() as db:
            await set_room_pinned(
                db, user_id=user.id, room_id=rooms[0].id, pinned=True
            )
            await db.commit()
            out = await reorder_pinned_rooms(
                db, user_id=user.id, room_ids=[]
            )
            await db.commit()
            assert out == [rooms[0].id]


# -- REST endpoint tests ------------------------------------------------------


class TestPinEndpoints:
    @pytest.mark.asyncio
    async def test_patch_pin_toggles(self, pin_env) -> None:
        app = pin_env["app"]
        token = pin_env["token"]
        rooms = pin_env["rooms"]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/v1/rooms/{rooms[0].id}/pin",
                json={"pinned": True},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200
            assert r.json() == {"pinned_room_ids": [rooms[0].id]}

            r = await client.patch(
                f"/api/v1/rooms/{rooms[0].id}/pin",
                json={"pinned": False},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200
            assert r.json() == {"pinned_room_ids": []}

    @pytest.mark.asyncio
    async def test_put_pin_order_snapshot(self, pin_env) -> None:
        app = pin_env["app"]
        token = pin_env["token"]
        rooms = pin_env["rooms"]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for r in rooms:
                await client.patch(
                    f"/api/v1/rooms/{r.id}/pin",
                    json={"pinned": True},
                    headers={"Authorization": f"Bearer {token}"},
                )

            snapshot = [rooms[2].id, rooms[0].id, rooms[1].id]
            r = await client.put(
                "/api/v1/rooms/pin-order",
                json={"room_ids": snapshot},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200
            assert r.json() == {"pinned_room_ids": snapshot}

    @pytest.mark.asyncio
    async def test_patch_pin_requires_auth(self, pin_env) -> None:
        app = pin_env["app"]
        rooms = pin_env["rooms"]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/v1/rooms/{rooms[0].id}/pin", json={"pinned": True}
            )
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_patch_pin_non_member_returns_404(self, pin_env) -> None:
        app = pin_env["app"]
        token = pin_env["token"]
        agent_room = pin_env["agent_room"]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.patch(
                f"/api/v1/rooms/{agent_room.id}/pin",
                json={"pinned": True},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 404
