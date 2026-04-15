"""Tests for Room CRUD, sub-room creation, and permission inheritance."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, Base, Participant, Project, Room, User


# -- Fixtures -----------------------------------------------------------------


@pytest_asyncio.fixture()
async def room_env(config: DooraeSettings):
    """Set up app with engine, session factory, user, project, and room seed data."""
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(email="room-test@doorae.io", password_hash="x")
        db.add(user)
        await db.flush()

        project = Project(name="room-proj")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="general")
        db.add(room)
        await db.flush()

        participant = Participant(room_id=room.id, user_id=user.id, role="owner")
        db.add(participant)
        await db.flush()

        await db.commit()
        await db.refresh(user)
        await db.refresh(project)
        await db.refresh(room)
        await db.refresh(participant)

        token = create_user_token(
            user.id, user.email, False, secret=config.jwt_secret
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = session_factory

        yield {
            "app": app,
            "user": user,
            "project": project,
            "room": room,
            "participant": participant,
            "token": token,
        }

    await engine.dispose()


# -- Room CRUD Tests ----------------------------------------------------------


class TestRoomCRUD:
    @pytest.mark.asyncio
    async def test_create_room(self, room_env) -> None:
        app = room_env["app"]
        project = room_env["project"]
        token = room_env["token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/rooms",
                json={"project_id": project.id, "name": "dev-chat"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["name"] == "dev-chat"
            assert data["project_id"] == project.id
            assert data["is_dm"] is False

    @pytest.mark.asyncio
    async def test_list_rooms(self, room_env) -> None:
        app = room_env["app"]
        project = room_env["project"]
        token = room_env["token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms?project_id={project.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) >= 1  # At least the seeded "general" room
            names = [r["name"] for r in data]
            assert "general" in names

    @pytest.mark.asyncio
    async def test_list_rooms_is_dm_filter(self, room_env) -> None:
        """is_dm filter separates DM rooms from regular rooms."""
        app = room_env["app"]
        project = room_env["project"]
        token = room_env["token"]

        # Create a DM room in the same project
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Seed a DM room directly
            from doorae.db.models import Room as RoomModel
            sf = app.state.session_factory
            async with sf() as db:
                dm = RoomModel(project_id=project.id, name="DM: test-bot", is_dm=True)
                db.add(dm)
                await db.commit()

            # is_dm=false should NOT include the DM
            resp = await client.get(
                f"/api/v1/rooms?project_id={project.id}&is_dm=false",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            names = [r["name"] for r in resp.json()]
            assert "general" in names
            assert "DM: test-bot" not in names

            # is_dm=true should ONLY include the DM
            resp = await client.get(
                f"/api/v1/rooms?project_id={project.id}&is_dm=true",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            names = [r["name"] for r in resp.json()]
            assert "DM: test-bot" in names
            assert "general" not in names

    @pytest.mark.asyncio
    async def test_get_room_detail(self, room_env) -> None:
        app = room_env["app"]
        room = room_env["room"]
        token = room_env["token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms/{room.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "general"
            assert len(data["participants"]) >= 1

    @pytest.mark.asyncio
    async def test_get_room_detail_renders_guest_display_name(self, room_env) -> None:
        """Guests have no email; the detail endpoint must fall back to
        display_name instead of crashing on ``None.split('@')``. The
        response also surfaces ``is_anonymous`` so the frontend can
        render a "Guest" badge in the participant list."""
        app = room_env["app"]
        room = room_env["room"]
        token = room_env["token"]

        sf = app.state.session_factory
        async with sf() as db:
            from doorae.db.models import Participant as P
            from doorae.db.models import User as U

            guest = U(
                email=None,
                password_hash=None,
                is_anonymous=True,
                display_name="Visitor",
            )
            db.add(guest)
            await db.flush()
            db.add(P(room_id=room.id, user_id=guest.id, role="member"))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/rooms/{room.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            parts = resp.json()["participants"]
            by_name = {p["display_name"]: p for p in parts}
            assert "Visitor" in by_name
            assert by_name["Visitor"]["is_anonymous"] is True
            # Registered owner in the same room must be marked not-anonymous.
            registered = [p for p in parts if p["display_name"] != "Visitor"]
            assert registered
            assert all(p["is_anonymous"] is False for p in registered)

    @pytest.mark.asyncio
    async def test_add_participant(self, room_env) -> None:
        app = room_env["app"]
        room = room_env["room"]
        user = room_env["user"]
        token = room_env["token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create a second room and add participant
            project = room_env["project"]
            resp = await client.post(
                "/api/v1/rooms",
                json={"project_id": project.id, "name": "new-room"},
                headers={"Authorization": f"Bearer {token}"},
            )
            new_room_id = resp.json()["id"]

            resp = await client.post(
                f"/api/v1/rooms/{new_room_id}/participants",
                json={"user_id": user.id, "role": "member"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["user_id"] == user.id
            assert data["role"] == "member"

    @pytest.mark.asyncio
    async def test_add_participant_notifies_user(self, room_env) -> None:
        """Adding an existing user to a new room pushes a
        ``room_membership_changed`` frame over the user's existing WS."""
        import json

        app = room_env["app"]
        existing_room = room_env["room"]
        existing_part = room_env["participant"]
        user = room_env["user"]
        token = room_env["token"]

        from doorae.ws.manager import ConnectionManager

        if not getattr(app.state, "connection_manager", None):
            app.state.connection_manager = ConnectionManager()
        manager = app.state.connection_manager

        received: list[str] = []

        class FakeWS:
            async def send_text(self, data: str) -> None:
                received.append(data)

        ws = FakeWS()
        await manager.subscribe(existing_room.id, existing_part.id, ws)  # type: ignore[arg-type]

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                project = room_env["project"]
                resp = await client.post(
                    "/api/v1/rooms",
                    json={"project_id": project.id, "name": "notif-target"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                new_room_id = resp.json()["id"]

                resp = await client.post(
                    f"/api/v1/rooms/{new_room_id}/participants",
                    json={"user_id": user.id, "role": "member"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 201
        finally:
            await manager.unsubscribe(existing_part.id)

        membership_frames = [
            json.loads(raw)
            for raw in received
            if json.loads(raw).get("type") == "room_membership_changed"
        ]
        assert len(membership_frames) == 1
        frame = membership_frames[0]
        assert frame["action"] == "added"
        assert frame["room_id"] == new_room_id
        assert frame["user_id"] == user.id

    @pytest.mark.asyncio
    async def test_delete_room(self, room_env) -> None:
        app = room_env["app"]
        project = room_env["project"]
        token = room_env["token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create a room to delete
            resp = await client.post(
                "/api/v1/rooms",
                json={"project_id": project.id, "name": "to-delete"},
                headers={"Authorization": f"Bearer {token}"},
            )
            room_id = resp.json()["id"]

            resp = await client.delete(
                f"/api/v1/rooms/{room_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 204

            # Verify it's gone
            resp = await client.get(
                f"/api/v1/rooms/{room_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unauthenticated_request_returns_401(self, room_env) -> None:
        """Requests without an auth token should be rejected."""
        app = room_env["app"]
        project = room_env["project"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/rooms?project_id={project.id}")
            assert resp.status_code == 401


class TestSubRoom:
    @pytest.mark.asyncio
    async def test_create_sub_room_with_permission(self, room_env) -> None:
        """Creator who is a member of the parent room can create a sub-room."""
        app = room_env["app"]
        room = room_env["room"]
        participant = room_env["participant"]
        token = room_env["token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{room.id}/sub-rooms",
                json={
                    "name": "sub-thread",
                    "participants": [],
                    "is_dm": False,
                    "creator_participant_id": participant.id,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["name"] == "sub-thread"
            assert data["parent_room_id"] == room.id


class TestRepresentativeAgent:
    """Tests for PUT /api/v1/rooms/{room_id}/representative."""

    @pytest_asyncio.fixture()
    async def rep_env(self, config: DooraeSettings):
        """Admin user + room + agent participant for representative tests."""
        engine = build_engine(config.db_url)
        session_factory = build_session_factory(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            admin = User(email="rep-admin@doorae.io", password_hash="x", is_admin=True)
            db.add(admin)
            await db.flush()

            project = Project(name="rep-proj")
            db.add(project)
            await db.flush()

            room = Room(project_id=project.id, name="rep-room")
            db.add(room)
            await db.flush()

            agent = Agent(name="bot-a", engine="codex")
            db.add(agent)
            await db.flush()

            # Agent is a participant of the room
            agent_part = Participant(room_id=room.id, agent_id=agent.id, role="member")
            db.add(agent_part)
            await db.flush()

            await db.commit()
            for obj in (admin, project, room, agent, agent_part):
                await db.refresh(obj)

            token = create_user_token(
                admin.id, admin.email, True, secret=config.jwt_secret
            )

            app = create_app(config)
            app.state.engine = engine
            app.state.session_factory = session_factory

            yield {
                "app": app,
                "room": room,
                "agent": agent,
                "token": token,
            }

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_set_representative(self, rep_env) -> None:
        """Admin can set a participant agent as representative."""
        app, room, agent, token = (
            rep_env["app"], rep_env["room"], rep_env["agent"], rep_env["token"]
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                f"/api/v1/rooms/{room.id}/representative",
                json={"agent_id": agent.id},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["representative_agent_id"] == agent.id

    @pytest.mark.asyncio
    async def test_clear_representative(self, rep_env) -> None:
        """Admin can clear representative by sending null."""
        app, room, agent, token = (
            rep_env["app"], rep_env["room"], rep_env["agent"], rep_env["token"]
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Set first
            await client.put(
                f"/api/v1/rooms/{room.id}/representative",
                json={"agent_id": agent.id},
                headers={"Authorization": f"Bearer {token}"},
            )
            # Clear
            resp = await client.put(
                f"/api/v1/rooms/{room.id}/representative",
                json={"agent_id": None},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            assert resp.json()["representative_agent_id"] is None

    @pytest.mark.asyncio
    async def test_set_representative_non_participant_fails(self, rep_env) -> None:
        """Setting a non-participant agent as representative returns 400."""
        app, room, token = rep_env["app"], rep_env["room"], rep_env["token"]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                f"/api/v1/rooms/{room.id}/representative",
                json={"agent_id": "nonexistent-agent-id"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 400
