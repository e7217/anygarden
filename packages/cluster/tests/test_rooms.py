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
    async def test_delete_room_member_forbidden(self, room_env) -> None:
        """Rank-and-file members cannot delete the room — only
        admin/owner can. Server enforces this regardless of the FE
        button gating."""
        app = room_env["app"]
        room = room_env["room"]
        sf = app.state.session_factory

        # Seed a member-role user in the room and mint their token.
        async with sf() as db:
            from doorae.auth.jwt import create_user_token as _mk_token
            from doorae.db.models import Participant as _P
            from doorae.db.models import User as _U

            member = _U(email="member@doorae.io", password_hash="x")
            db.add(member)
            await db.flush()
            db.add(_P(room_id=room.id, user_id=member.id, role="member"))
            await db.commit()
            await db.refresh(member)
        # Use the env's existing config to sign — the server in
        # ``room_env`` was built from the same one.
        member_token = _mk_token(
            member.id,
            member.email,
            False,
            secret=app.state.config.jwt_secret,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}",
                headers={"Authorization": f"Bearer {member_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_room_outsider_403_not_404(self, room_env) -> None:
        """Outsider attempting to delete an unrelated/unknown room
        gets 403 — never 404. Otherwise an attacker probing UUIDs
        could enumerate room existence by 403-vs-404 timing, the
        same oracle the invite endpoints already close (#25)."""
        app = room_env["app"]
        sf = app.state.session_factory

        async with sf() as db:
            from doorae.auth.jwt import create_user_token as _mk_token
            from doorae.db.models import User as _U

            outsider = _U(email="outsider@doorae.io", password_hash="x")
            db.add(outsider)
            await db.commit()
            await db.refresh(outsider)
        outsider_token = _mk_token(
            outsider.id,
            outsider.email,
            False,
            secret=app.state.config.jwt_secret,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                "/api/v1/rooms/does-not-exist",
                headers={"Authorization": f"Bearer {outsider_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_room_global_admin_can_delete_unrelated(
        self, room_env
    ) -> None:
        """Global admins (``User.is_admin``) can delete any room
        even if they aren't a Participant — same rule as invite
        management."""
        app = room_env["app"]
        room = room_env["room"]
        sf = app.state.session_factory

        async with sf() as db:
            from doorae.auth.jwt import create_user_token as _mk_token
            from doorae.db.models import User as _U

            admin = _U(email="g-admin@doorae.io", password_hash="x", is_admin=True)
            db.add(admin)
            await db.commit()
            await db.refresh(admin)
        admin_token = _mk_token(
            admin.id,
            admin.email,
            True,
            secret=app.state.config.jwt_secret,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_room_broadcasts_room_deleted(self, room_env) -> None:
        """Successful delete pushes ``RoomDeletedOut`` to subscribers
        of the deleted room AND to each member's other active WS
        sessions (sibling-room sidebars). The frontend depends on
        this push for snappy sidebar refresh — see useWebSocket's
        ``room_deleted`` branch."""
        import json

        app = room_env["app"]
        room = room_env["room"]
        token = room_env["token"]
        owner_part = room_env["participant"]
        sf = app.state.session_factory

        from doorae.ws.manager import ConnectionManager

        if not getattr(app.state, "connection_manager", None):
            app.state.connection_manager = ConnectionManager()
        manager = app.state.connection_manager

        # Seed a sibling room with the same owner — gives us a
        # second participant_id under the same user_id, which is
        # what the "other-WS push" path reaches for.
        async with sf() as db:
            from doorae.db.models import Participant as _P
            from doorae.db.models import Project as _Proj
            from doorae.db.models import Room as _R

            other_proj = _Proj(name="sib-proj")
            db.add(other_proj)
            await db.flush()
            sibling = _R(project_id=other_proj.id, name="sibling")
            db.add(sibling)
            await db.flush()
            sib_part = _P(
                room_id=sibling.id,
                user_id=room_env["user"].id,
                role="owner",
            )
            db.add(sib_part)
            await db.commit()
            await db.refresh(sib_part)

        deleted_received: list[str] = []
        sibling_received: list[str] = []

        class FakeWS:
            def __init__(self, sink: list[str]) -> None:
                self._sink = sink

            async def send_text(self, data: str) -> None:
                self._sink.append(data)

        # Two WS subscriptions for the same user: one in the room
        # being deleted, one in a sibling room.
        await manager.subscribe(room.id, owner_part.id, FakeWS(deleted_received))  # type: ignore[arg-type]
        await manager.subscribe(
            sibling.id, sib_part.id, FakeWS(sibling_received)
        )  # type: ignore[arg-type]

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.delete(
                    f"/api/v1/rooms/{room.id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 204
        finally:
            await manager.unsubscribe(owner_part.id)
            await manager.unsubscribe(sib_part.id)

        def _frames(sink: list[str]) -> list[dict]:
            return [
                json.loads(raw)
                for raw in sink
                if json.loads(raw).get("type") == "room_deleted"
            ]

        deleted_frames = _frames(deleted_received)
        sibling_frames = _frames(sibling_received)
        assert len(deleted_frames) >= 1
        assert deleted_frames[0]["room_id"] == room.id
        # Sibling-room WS also got pinged so the sidebar can refresh
        # without having to reach the deleted room's stale socket.
        assert len(sibling_frames) >= 1
        assert sibling_frames[0]["room_id"] == room.id

    @pytest.mark.asyncio
    async def test_delete_room_archives_child_rooms(self, room_env) -> None:
        """Child rooms aren't deleted with the parent — they detach
        (parent_room_id → NULL). This is the ``archive_child_rooms``
        contract, pinned here so a future cleanup that switches to
        cascade-delete doesn't silently destroy users' content."""
        app = room_env["app"]
        room = room_env["room"]
        token = room_env["token"]
        sf = app.state.session_factory

        async with sf() as db:
            from doorae.db.models import Room as _R

            child = _R(
                project_id=room.project_id,
                name="child",
                parent_room_id=room.id,
            )
            db.add(child)
            await db.commit()
            await db.refresh(child)
            child_id = child.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 204

        async with sf() as db:
            from sqlalchemy import select as _select

            from doorae.db.models import Room as _R

            reloaded = (
                await db.execute(_select(_R).where(_R.id == child_id))
            ).scalar_one()
            # Child still exists, just orphaned to project root.
            assert reloaded.parent_room_id is None

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


class TestRemoveParticipant:
    """Tests for DELETE /api/v1/rooms/{room_id}/participants/{participant_id}."""

    @pytest_asyncio.fixture()
    async def removal_env(self, config: DooraeSettings):
        """Room with an owner, a regular member, an agent (representative),
        and an outsider (not a member of the room)."""
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

            project = Project(name="rm-proj")
            db.add(project)
            await db.flush()

            agent = Agent(name="rep-bot", engine="codex")
            db.add(agent)
            await db.flush()

            room = Room(
                project_id=project.id,
                name="rm-room",
                representative_agent_id=agent.id,
            )
            db.add(room)
            await db.flush()

            owner_part = Participant(room_id=room.id, user_id=owner.id, role="owner")
            member_part = Participant(room_id=room.id, user_id=member.id, role="member")
            agent_part = Participant(room_id=room.id, agent_id=agent.id, role="member")
            db.add_all([owner_part, member_part, agent_part])
            await db.flush()

            await db.commit()
            for obj in (owner, member, outsider, admin, project, agent, room,
                        owner_part, member_part, agent_part):
                await db.refresh(obj)

            owner_token = create_user_token(
                owner.id, owner.email, False, secret=config.jwt_secret
            )
            member_token = create_user_token(
                member.id, member.email, False, secret=config.jwt_secret
            )
            outsider_token = create_user_token(
                outsider.id, outsider.email, False, secret=config.jwt_secret
            )
            admin_token = create_user_token(
                admin.id, admin.email, True, secret=config.jwt_secret
            )

            app = create_app(config)
            app.state.engine = engine
            app.state.session_factory = session_factory

            yield {
                "app": app,
                "config": config,
                "room": room,
                "agent": agent,
                "owner": owner,
                "member": member,
                "outsider": outsider,
                "admin": admin,
                "owner_part": owner_part,
                "member_part": member_part,
                "agent_part": agent_part,
                "owner_token": owner_token,
                "member_token": member_token,
                "outsider_token": outsider_token,
                "admin_token": admin_token,
            }

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_owner_removes_user_participant(self, removal_env) -> None:
        """Owner removes a regular user: 204, row gone, broadcast received."""
        import json

        app = removal_env["app"]
        room = removal_env["room"]
        owner_part = removal_env["owner_part"]
        member_part = removal_env["member_part"]
        owner_token = removal_env["owner_token"]

        from doorae.ws.manager import ConnectionManager

        if not getattr(app.state, "connection_manager", None):
            app.state.connection_manager = ConnectionManager()
        manager = app.state.connection_manager

        received: list[str] = []

        class FakeWS:
            async def send_text(self, data: str) -> None:
                received.append(data)

        ws = FakeWS()
        # Subscribe the owner's WS — they must receive the broadcast.
        await manager.subscribe(room.id, owner_part.id, ws)  # type: ignore[arg-type]

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(
                    f"/api/v1/rooms/{room.id}/participants/{member_part.id}",
                    headers={"Authorization": f"Bearer {owner_token}"},
                )
                assert resp.status_code == 204

                # Row is gone
                sf = app.state.session_factory
                async with sf() as db:
                    from sqlalchemy import select as _select
                    result = await db.execute(
                        _select(Participant).where(Participant.id == member_part.id)
                    )
                    assert result.scalar_one_or_none() is None
        finally:
            await manager.unsubscribe(owner_part.id)

        frames = [json.loads(raw) for raw in received]
        removed = [f for f in frames if f.get("type") == "room_membership_changed"]
        assert len(removed) == 1
        assert removed[0]["action"] == "removed"
        assert removed[0]["room_id"] == room.id
        assert removed[0]["user_id"] == removal_env["member"].id

    @pytest.mark.asyncio
    async def test_removed_participant_does_not_receive_broadcast(self, removal_env) -> None:
        """Regression guard: ``RoomMembershipChangedOut(action="removed")``
        must reach *remaining* subscribers only. A future refactor that
        loops over every Participant (including the one about to be
        deleted) would still pass ``test_owner_removes_user_participant``
        — this asserts the audience is filtered.
        """
        import json

        app = removal_env["app"]
        room = removal_env["room"]
        owner_part = removal_env["owner_part"]
        member_part = removal_env["member_part"]
        owner_token = removal_env["owner_token"]

        from doorae.ws.manager import ConnectionManager

        if not getattr(app.state, "connection_manager", None):
            app.state.connection_manager = ConnectionManager()
        manager = app.state.connection_manager

        owner_received: list[str] = []
        removed_received: list[str] = []

        class FakeWS:
            def __init__(self, sink: list[str]) -> None:
                self._sink = sink

            async def send_text(self, data: str) -> None:
                self._sink.append(data)

        owner_ws = FakeWS(owner_received)
        removed_ws = FakeWS(removed_received)
        await manager.subscribe(room.id, owner_part.id, owner_ws)  # type: ignore[arg-type]
        await manager.subscribe(room.id, member_part.id, removed_ws)  # type: ignore[arg-type]

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(
                    f"/api/v1/rooms/{room.id}/participants/{member_part.id}",
                    headers={"Authorization": f"Bearer {owner_token}"},
                )
                assert resp.status_code == 204
        finally:
            await manager.unsubscribe(owner_part.id)
            await manager.unsubscribe(member_part.id)

        owner_frames = [
            json.loads(raw)
            for raw in owner_received
            if json.loads(raw).get("type") == "room_membership_changed"
        ]
        removed_frames = [
            json.loads(raw)
            for raw in removed_received
            if json.loads(raw).get("type") == "room_membership_changed"
        ]
        assert len(owner_frames) == 1
        assert owner_frames[0]["action"] == "removed"
        # Departed participant must NOT receive the removal broadcast.
        assert removed_frames == []

    @pytest.mark.asyncio
    async def test_owner_removes_agent_clears_representative(self, removal_env) -> None:
        """Removing the representative agent must clear ``Room.representative_agent_id``."""
        app = removal_env["app"]
        room = removal_env["room"]
        agent_part = removal_env["agent_part"]
        owner_token = removal_env["owner_token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}/participants/{agent_part.id}",
                headers={"Authorization": f"Bearer {owner_token}"},
            )
            assert resp.status_code == 204

        sf = app.state.session_factory
        async with sf() as db:
            from sqlalchemy import select as _select
            result = await db.execute(_select(Room).where(Room.id == room.id))
            reloaded = result.scalar_one()
            assert reloaded.representative_agent_id is None

    @pytest.mark.asyncio
    async def test_rank_and_file_member_cannot_remove(self, removal_env) -> None:
        """A regular member gets 403 when attempting removal."""
        app = removal_env["app"]
        room = removal_env["room"]
        agent_part = removal_env["agent_part"]
        member_token = removal_env["member_token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}/participants/{agent_part.id}",
                headers={"Authorization": f"Bearer {member_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_outsider_gets_403_not_404(self, removal_env) -> None:
        """Non-member, non-admin gets 403 even for unknown participant/room IDs
        to avoid existence enumeration."""
        app = removal_env["app"]
        room = removal_env["room"]
        outsider_token = removal_env["outsider_token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Unknown participant id inside a real room
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}/participants/does-not-exist",
                headers={"Authorization": f"Bearer {outsider_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_guest_forbidden(self, removal_env) -> None:
        """A guest token is rejected by ``forbid_guest`` before any DB work."""
        from doorae.auth.jwt import create_guest_token
        from datetime import datetime, timedelta, timezone

        app = removal_env["app"]
        room = removal_env["room"]
        member_part = removal_env["member_part"]
        config = removal_env["config"]

        # Create a guest user and token bound to this room
        sf = app.state.session_factory
        async with sf() as db:
            guest = User(
                email=None,
                password_hash=None,
                is_anonymous=True,
                display_name="G",
            )
            db.add(guest)
            await db.commit()
            await db.refresh(guest)

        guest_token = create_guest_token(
            user_id=guest.id,
            room_id=room.id,
            invite_id="dummy",
            display_name="G",
            secret=config.jwt_secret,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}/participants/{member_part.id}",
                headers={"Authorization": f"Bearer {guest_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_self_removal_returns_400(self, removal_env) -> None:
        """Owner trying to remove themselves via this endpoint gets 400."""
        app = removal_env["app"]
        room = removal_env["room"]
        owner_part = removal_env["owner_part"]
        owner_token = removal_env["owner_token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}/participants/{owner_part.id}",
                headers={"Authorization": f"Bearer {owner_token}"},
            )
            assert resp.status_code == 400
            assert "leave-room" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_removing_last_admin_returns_409(self, config: DooraeSettings) -> None:
        """Removing the only admin/owner of a room returns 409."""
        engine = build_engine(config.db_url)
        session_factory = build_session_factory(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            global_admin = User(email="ga@doorae.io", password_hash="x", is_admin=True)
            sole_owner = User(email="sole@doorae.io", password_hash="x")
            db.add_all([global_admin, sole_owner])
            await db.flush()

            project = Project(name="la-proj")
            db.add(project)
            await db.flush()

            room = Room(project_id=project.id, name="la-room")
            db.add(room)
            await db.flush()

            sole_part = Participant(room_id=room.id, user_id=sole_owner.id, role="owner")
            db.add(sole_part)
            await db.flush()

            await db.commit()
            for obj in (global_admin, sole_owner, project, room, sole_part):
                await db.refresh(obj)

            admin_token = create_user_token(
                global_admin.id, global_admin.email, True, secret=config.jwt_secret
            )

            app = create_app(config)
            app.state.engine = engine
            app.state.session_factory = session_factory

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(
                    f"/api/v1/rooms/{room.id}/participants/{sole_part.id}",
                    headers={"Authorization": f"Bearer {admin_token}"},
                )
                assert resp.status_code == 409
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_unknown_participant_with_admin_returns_404(self, removal_env) -> None:
        """Admin caller + non-existent participant id → 404."""
        app = removal_env["app"]
        room = removal_env["room"]
        admin_token = removal_env["admin_token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}/participants/does-not-exist",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_global_admin_can_remove_without_membership(self, removal_env) -> None:
        """Global admin is not a room member but can still remove."""
        app = removal_env["app"]
        room = removal_env["room"]
        member_part = removal_env["member_part"]
        admin_token = removal_env["admin_token"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}/participants/{member_part.id}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code == 204
