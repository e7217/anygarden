"""Tests for sidebar unread-update indicators (#385)."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Base, Message, Participant, Project, Room, User
from doorae.rooms.unread import compute_has_updates_map, mark_room_read


@pytest_asyncio.fixture()
async def unread_env():
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        user = User(email="reader@test.com", password_hash="x")
        other = User(email="other@test.com", password_hash="x")
        db.add_all([user, other])
        await db.flush()

        project = Project(name="updates")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="general")
        empty_room = Room(project_id=project.id, name="empty")
        other_room = Room(project_id=project.id, name="other-only")
        db.add_all([room, empty_room, other_room])
        await db.flush()

        participant = Participant(room_id=room.id, user_id=user.id, role="owner")
        empty_participant = Participant(
            room_id=empty_room.id, user_id=user.id, role="member"
        )
        other_participant = Participant(
            room_id=other_room.id, user_id=other.id, role="owner"
        )
        db.add_all([participant, empty_participant, other_participant])
        await db.flush()

        db.add_all([
            Message(
                room_id=room.id,
                participant_id=participant.id,
                content="first",
                seq=1,
            ),
            Message(
                room_id=room.id,
                participant_id=participant.id,
                content="second",
                seq=2,
            ),
            Message(
                room_id=other_room.id,
                participant_id=other_participant.id,
                content="hidden",
                seq=1,
            ),
        ])
        await db.commit()

        token = create_user_token(
            user.id, user.email or "", False, secret=config.jwt_secret
        )
        other_token = create_user_token(
            other.id, other.email or "", False, secret=config.jwt_secret
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory

        yield {
            "app": app,
            "factory": factory,
            "user": user,
            "other": other,
            "project": project,
            "room": room,
            "empty_room": empty_room,
            "other_room": other_room,
            "participant": participant,
            "token": token,
            "other_token": other_token,
        }

    await engine.dispose()


class TestSchema:
    def test_participant_has_last_read_column(self) -> None:
        assert "last_read_message_seq" in Participant.__table__.columns
        column = Participant.__table__.columns["last_read_message_seq"]
        assert column.nullable is True


class TestComputeHasUpdates:
    @pytest.mark.asyncio
    async def test_null_last_read_with_messages_is_true(self, unread_env) -> None:
        async with unread_env["factory"]() as db:
            result = await compute_has_updates_map(
                db,
                user_id=unread_env["user"].id,
                room_ids=[unread_env["room"].id],
            )
        assert result == {unread_env["room"].id: True}

    @pytest.mark.asyncio
    async def test_null_last_read_empty_room_is_false(self, unread_env) -> None:
        async with unread_env["factory"]() as db:
            result = await compute_has_updates_map(
                db,
                user_id=unread_env["user"].id,
                room_ids=[unread_env["empty_room"].id],
            )
        assert result == {unread_env["empty_room"].id: False}

    @pytest.mark.asyncio
    async def test_last_read_equals_max_seq_is_false(self, unread_env) -> None:
        async with unread_env["factory"]() as db:
            participant = await db.get(Participant, unread_env["participant"].id)
            assert participant is not None
            participant.last_read_message_seq = 2
            await db.commit()

            result = await compute_has_updates_map(
                db,
                user_id=unread_env["user"].id,
                room_ids=[unread_env["room"].id],
            )
        assert result == {unread_env["room"].id: False}

    @pytest.mark.asyncio
    async def test_last_read_less_than_max_seq_is_true(self, unread_env) -> None:
        async with unread_env["factory"]() as db:
            participant = await db.get(Participant, unread_env["participant"].id)
            assert participant is not None
            participant.last_read_message_seq = 1
            await db.commit()

            result = await compute_has_updates_map(
                db,
                user_id=unread_env["user"].id,
                room_ids=[unread_env["room"].id],
            )
        assert result == {unread_env["room"].id: True}

    @pytest.mark.asyncio
    async def test_non_member_room_not_in_map(self, unread_env) -> None:
        async with unread_env["factory"]() as db:
            result = await compute_has_updates_map(
                db,
                user_id=unread_env["user"].id,
                room_ids=[unread_env["other_room"].id],
            )
        assert result == {}


class TestMarkRoomRead:
    @pytest.mark.asyncio
    async def test_mark_room_read_sets_last_read_to_max(self, unread_env) -> None:
        async with unread_env["factory"]() as db:
            seq = await mark_room_read(
                db, user_id=unread_env["user"].id, room_id=unread_env["room"].id
            )
            await db.commit()
            participant = await db.get(Participant, unread_env["participant"].id)
        assert seq == 2
        assert participant is not None
        assert participant.last_read_message_seq == 2

    @pytest.mark.asyncio
    async def test_mark_room_read_is_monotonic(self, unread_env) -> None:
        async with unread_env["factory"]() as db:
            participant = await db.get(Participant, unread_env["participant"].id)
            assert participant is not None
            participant.last_read_message_seq = 99
            await db.commit()

            seq = await mark_room_read(
                db, user_id=unread_env["user"].id, room_id=unread_env["room"].id
            )
            await db.commit()
            participant = await db.get(Participant, unread_env["participant"].id)
        assert seq == 99
        assert participant is not None
        assert participant.last_read_message_seq == 99


class TestMarkReadEndpoint:
    @pytest.mark.asyncio
    async def test_post_read_sets_last_read_to_max(self, unread_env) -> None:
        transport = ASGITransport(app=unread_env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{unread_env['room'].id}/read",
                headers={"Authorization": f"Bearer {unread_env['token']}"},
            )
        assert resp.status_code == 200
        assert resp.json()["last_read_message_seq"] == 2

    @pytest.mark.asyncio
    async def test_post_read_non_member_returns_403(self, unread_env) -> None:
        transport = ASGITransport(app=unread_env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{unread_env['other_room'].id}/read",
                headers={"Authorization": f"Bearer {unread_env['token']}"},
            )
        assert resp.status_code == 403


class TestListRoomsHasUpdates:
    @pytest.mark.asyncio
    async def test_list_rooms_returns_has_updates_per_room(self, unread_env) -> None:
        transport = ASGITransport(app=unread_env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms?project_id={unread_env['project'].id}",
                headers={"Authorization": f"Bearer {unread_env['token']}"},
            )
        assert resp.status_code == 200
        by_id = {room["id"]: room for room in resp.json()}
        assert by_id[unread_env["room"].id]["has_updates"] is True
        assert by_id[unread_env["empty_room"].id]["has_updates"] is False
        assert by_id[unread_env["other_room"].id]["has_updates"] is False

    @pytest.mark.asyncio
    async def test_list_rooms_has_updates_false_after_mark_read(self, unread_env) -> None:
        transport = ASGITransport(app=unread_env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            mark = await client.post(
                f"/api/v1/rooms/{unread_env['room'].id}/read",
                headers={"Authorization": f"Bearer {unread_env['token']}"},
            )
            assert mark.status_code == 200

            resp = await client.get(
                f"/api/v1/rooms?project_id={unread_env['project'].id}",
                headers={"Authorization": f"Bearer {unread_env['token']}"},
            )
        assert resp.status_code == 200
        by_id = {room["id"]: room for room in resp.json()}
        assert by_id[unread_env["room"].id]["has_updates"] is False

    @pytest.mark.asyncio
    async def test_other_user_has_independent_read_state(self, unread_env) -> None:
        transport = ASGITransport(app=unread_env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms?project_id={unread_env['project'].id}",
                headers={"Authorization": f"Bearer {unread_env['other_token']}"},
            )
        assert resp.status_code == 200
        by_id = {room["id"]: room for room in resp.json()}
        assert by_id[unread_env["other_room"].id]["has_updates"] is True
        assert by_id[unread_env["room"].id]["has_updates"] is False
