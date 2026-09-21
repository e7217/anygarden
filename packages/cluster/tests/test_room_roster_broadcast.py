"""Integration tests for runtime room-roster propagation (#644).

Before #644 the participant roster (and every field rendered into it —
``Agent.name``, ``Agent.description``) was delivered *only* in the WS
``welcome`` frame. A membership change or a description edit therefore
never reached an already-connected agent: its ``_participants_by_room``
cache stayed frozen at connect time, so newcomers were invisible,
departed peers lingered, and an edited introduction was ignored until
the agent reconnected.

These tests pin the fix: every mutation that changes what a roster line
renders must emit a ``room_settings_changed`` frame carrying the full
refreshed ``participants`` snapshot. Mutations that *don't* touch the
roster (avatar, room rename) must stay silent so the wire doesn't carry
pointless traffic — the same "quiet unless cached state changed" rule
#221 established for the settings fields.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, Base, Participant, Project, Room, User
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.ws.manager import ConnectionManager
from anygarden.ws.protocol import RoomSettingsChangedOut


@pytest_asyncio.fixture()
async def roster_env(config: AnygardenSettings):
    """Admin + room with one agent participant, plus a spare agent and
    user that tests can add to the room."""
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        admin = User(
            email="roster-admin@anygarden.io", password_hash="x", is_admin=True
        )
        db.add(admin)
        spare_user = User(email="roster-spare@anygarden.io", password_hash="x")
        db.add(spare_user)
        await db.flush()

        project = Project(name="roster-proj")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="roster-room")
        db.add(room)
        await db.flush()

        seated = Agent(name="seated-bot", engine="codex", description="원래 있던 봇")
        db.add(seated)
        spare = Agent(name="spare-bot", engine="codex", description="새로 들어올 봇")
        db.add(spare)
        await db.flush()

        seated_part = Participant(
            room_id=room.id, agent_id=seated.id, role="member"
        )
        db.add(seated_part)
        await db.commit()
        for obj in (admin, spare_user, project, room, seated, spare, seated_part):
            await db.refresh(obj)

        token = create_user_token(
            admin.id, admin.email, True, secret=config.jwt_secret
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = session_factory
        # ``PUT /agents/{id}`` serialises through ``machine_bus`` and
        # bumps generation via ``agent_lifecycle`` — both are wired by
        # the lifespan in production, so tests must supply them.
        bus = MachineBus()
        app.state.machine_bus = bus
        app.state.agent_lifecycle = AgentLifecycle(
            db_factory=session_factory, machine_bus=bus
        )

        yield {
            "app": app,
            "room": room,
            "seated": seated,
            "seated_participant": seated_part,
            "spare": spare,
            "spare_user": spare_user,
            "token": token,
        }

    await engine.dispose()


def _spy_on_broadcast(app) -> list[object]:
    """Install a real ConnectionManager and capture broadcast frames.

    The fixtures don't run the app lifespan, so ``connection_manager``
    is absent — mirrors the approach in ``test_rooms.py``.
    """
    app.state.connection_manager = ConnectionManager()
    captured: list[object] = []
    original = app.state.connection_manager.broadcast

    async def spy(room_id, frame, exclude_participant_id=None):
        captured.append(frame)
        return await original(
            room_id, frame, exclude_participant_id=exclude_participant_id
        )

    app.state.connection_manager.broadcast = spy  # type: ignore[method-assign]
    return captured


def _roster_frames(captured: list[object]) -> list[RoomSettingsChangedOut]:
    return [
        f
        for f in captured
        if isinstance(f, RoomSettingsChangedOut) and f.participants is not None
    ]


class TestMembershipBroadcast:
    """Adding or removing a participant refreshes every connected
    peer's roster without a reconnect."""

    @pytest.mark.asyncio
    async def test_add_agent_participant_broadcasts_roster(
        self, roster_env
    ) -> None:
        app, room, spare = (
            roster_env["app"],
            roster_env["room"],
            roster_env["spare"],
        )
        captured = _spy_on_broadcast(app)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{room.id}/participants",
                json={"agent_id": spare.id, "role": "member"},
                headers={"Authorization": f"Bearer {roster_env['token']}"},
            )
            assert resp.status_code == 201

        frames = _roster_frames(captured)
        assert len(frames) == 1
        frame = frames[0]
        assert frame.room_id == room.id
        names = {p.display_name for p in frame.participants}
        # Both the pre-existing and the newly added agent are present —
        # the frame carries a full snapshot, not a delta.
        assert names == {"seated-bot", "spare-bot"}
        # Settings fields stay None: this frame is roster-only, and the
        # receiver must not reset its cached strategy because of it.
        assert frame.speaker_strategy is None
        assert frame.ephemeral is None

    @pytest.mark.asyncio
    async def test_add_user_participant_broadcasts_roster(
        self, roster_env
    ) -> None:
        app, room, spare_user = (
            roster_env["app"],
            roster_env["room"],
            roster_env["spare_user"],
        )
        captured = _spy_on_broadcast(app)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/rooms/{room.id}/participants",
                json={"user_id": spare_user.id, "role": "member"},
                headers={"Authorization": f"Bearer {roster_env['token']}"},
            )
            assert resp.status_code == 201

        frames = _roster_frames(captured)
        assert len(frames) == 1
        kinds = {p.kind for p in frames[0].participants}
        assert kinds == {"agent", "user"}

    @pytest.mark.asyncio
    async def test_remove_participant_broadcasts_roster(
        self, roster_env
    ) -> None:
        app, room, seated_part = (
            roster_env["app"],
            roster_env["room"],
            roster_env["seated_participant"],
        )
        captured = _spy_on_broadcast(app)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/rooms/{room.id}/participants/{seated_part.id}",
                headers={"Authorization": f"Bearer {roster_env['token']}"},
            )
            assert resp.status_code == 204

        frames = _roster_frames(captured)
        assert len(frames) == 1
        # The departed agent is gone from the snapshot — the whole point
        # of re-sending it rather than trusting the connect-time cache.
        assert all(p.display_name != "seated-bot" for p in frames[0].participants)


class TestAgentMetadataBroadcast:
    """Editing a field that a roster line *renders* must reach peers;
    editing one it doesn't must stay off the wire."""

    @pytest.mark.asyncio
    async def test_description_patch_broadcasts_roster(self, roster_env) -> None:
        """#271 introduced ``description`` as the peer-facing intro and
        documented that peers only see edits 'on their next welcome'.
        That caveat is the bug: the intro is the LLM's only basis for
        choosing whom to ask, so a stale one misroutes work."""
        app, room, seated = (
            roster_env["app"],
            roster_env["room"],
            roster_env["seated"],
        )
        captured = _spy_on_broadcast(app)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                f"/api/v1/agents/{seated.id}",
                json={
                    "description": "검증 담당. 배정된 검증 작업을 수행한다.",
                    "description_set": True,
                },
                headers={"Authorization": f"Bearer {roster_env['token']}"},
            )
            assert resp.status_code == 200

        frames = _roster_frames(captured)
        assert len(frames) == 1
        assert frames[0].room_id == room.id
        entry = next(
            p for p in frames[0].participants if p.display_name == "seated-bot"
        )
        assert entry.description == "검증 담당. 배정된 검증 작업을 수행한다."

    @pytest.mark.asyncio
    async def test_name_patch_broadcasts_roster(self, roster_env) -> None:
        """``name`` is the roster's ``display_name`` — peers address
        each other by it in prose, so a rename must propagate too."""
        app, seated = roster_env["app"], roster_env["seated"]
        captured = _spy_on_broadcast(app)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                f"/api/v1/agents/{seated.id}",
                json={"name": "renamed-bot"},
                headers={"Authorization": f"Bearer {roster_env['token']}"},
            )
            assert resp.status_code == 200

        frames = _roster_frames(captured)
        assert len(frames) == 1
        assert any(
            p.display_name == "renamed-bot" for p in frames[0].participants
        )

    @pytest.mark.asyncio
    async def test_avatar_patch_does_not_broadcast_roster(
        self, roster_env
    ) -> None:
        """Avatar is frontend-only — ``ParticipantBrief`` doesn't carry
        it, so emitting a roster frame would be pure noise."""
        app, seated = roster_env["app"], roster_env["seated"]
        captured = _spy_on_broadcast(app)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                f"/api/v1/agents/{seated.id}",
                json={"avatar_kind": "emoji", "avatar_kind_set": True},
                headers={"Authorization": f"Bearer {roster_env['token']}"},
            )
            assert resp.status_code == 200

        assert _roster_frames(captured) == []
