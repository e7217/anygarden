"""Tests for the /api/v1/agents REST endpoints."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    ActivityLog,
    Agent,
    AgentFile,
    Base,
    Machine,
    MachineEngine,
    Participant,
    Project,
    Room,
    User,
)
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus


@pytest_asyncio.fixture()
async def agents_env():
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    class FakeWS:
        async def send_text(self, data: str) -> None:
            pass

    async with factory() as db:
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        regular = User(email="regular@test.com", password_hash="x", is_admin=False)
        db.add_all([admin, regular])
        await db.flush()

        # Create a machine so agents can be placed
        machine = Machine(
            name="agents-machine",
            hostname="host-agents",
            owner_user_id=admin.id,
            status="online",
            max_agents=10,
        )
        db.add(machine)
        await db.flush()
        db.add(MachineEngine(machine_id=machine.id, engine="echo"))

        project = Project(name="test-project")
        db.add(project)
        await db.flush()

        await db.commit()

        await bus.register(machine.id, FakeWS())

        admin_token = create_user_token(
            admin.id, admin.email, admin.is_admin, secret=config.jwt_secret
        )
        regular_token = create_user_token(
            regular.id, regular.email, regular.is_admin, secret=config.jwt_secret
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory
        app.state.machine_bus = bus
        app.state.agent_lifecycle = lifecycle

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "token": admin_token,
                "regular_token": regular_token,
                "factory": factory,
                "admin": admin,
                "regular": regular,
                "machine": machine,
            }

    await engine.dispose()


class TestAgentsAPI:
    @pytest.mark.asyncio
    async def test_create_agent(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "test-agent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-agent"
        assert data["engine"] == "echo"
        # DM room auto-created → agent starts immediately
        assert data["desired_state"] == "running"
        # Issue #73 — runtime defaults to "python" for unqualified creates.
        assert data["runtime"] == "python"

    @pytest.mark.asyncio
    async def test_create_agent_with_typescript_runtime(self, agents_env) -> None:
        """Issue #73 — ``runtime='typescript'`` persists to the DB and
        echoes back in the response so the admin UI can render the
        runtime badge without a second GET."""
        client = agents_env["client"]
        token = agents_env["token"]
        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "ts-agent",
                "runtime": "typescript",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["runtime"] == "typescript"

    @pytest.mark.asyncio
    async def test_update_agent_runtime(self, agents_env) -> None:
        """Issue #73 — runtime is editable via PUT with the
        ``runtime_set`` flag. Bumps generation so the machine respawns
        with the new runtime."""
        client = agents_env["client"]
        token = agents_env["token"]
        create = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "mutable"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = create.json()["id"]
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"runtime": "typescript", "runtime_set": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["runtime"] == "typescript"

    @pytest.mark.asyncio
    async def test_list_agents(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        # Create an agent first
        await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "list-agent"},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await client.get(
            "/api/v1/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        names = [a["name"] for a in data]
        assert "list-agent" in names

    @pytest.mark.asyncio
    async def test_delete_agent(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        # Create
        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "del-agent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        # Delete
        resp = await client.delete(
            f"/api/v1/agents/{agent_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True

    @pytest.mark.asyncio
    async def test_delete_agent_also_removes_dm_room(self, agents_env) -> None:
        """Auto-created DM rooms must not outlive their owning agent.

        Regression test: the sidebar's "Agents" section lists every
        is_dm=True room, so an orphan DM surfaces as a ghost entry
        the admin can never clear.
        """
        from doorae.db.models import Room as RoomModel

        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        # Create — auto-creates a DM room named "DM: <name>"
        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "dm-del-agent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        # Precondition: DM room exists
        async with factory() as db:
            dm = (
                await db.execute(
                    select(RoomModel).where(
                        RoomModel.is_dm.is_(True),
                        RoomModel.name == "DM: dm-del-agent",
                    )
                )
            ).scalar_one_or_none()
            assert dm is not None

        # Delete the agent
        resp = await client.delete(
            f"/api/v1/agents/{agent_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Postcondition: DM room is gone
        async with factory() as db:
            dm = (
                await db.execute(
                    select(RoomModel).where(
                        RoomModel.is_dm.is_(True),
                        RoomModel.name == "DM: dm-del-agent",
                    )
                )
            ).scalar_one_or_none()
            assert dm is None

    @pytest.mark.asyncio
    async def test_non_admin_cannot_create_agent(self, agents_env) -> None:
        """Regular users must be blocked from creating agents."""
        client = agents_env["client"]
        regular_token = agents_env["regular_token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "unauthorized"},
            headers={"Authorization": f"Bearer {regular_token}"},
        )
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_non_admin_cannot_list_agents(self, agents_env) -> None:
        client = agents_env["client"]
        regular_token = agents_env["regular_token"]

        resp = await client.get(
            "/api/v1/agents",
            headers={"Authorization": f"Bearer {regular_token}"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_non_admin_cannot_add_agent_to_room(self, agents_env) -> None:
        """Regular users must not be able to join an agent to a room."""
        from doorae.db.models import Project, Room

        client = agents_env["client"]
        admin_token = agents_env["token"]
        regular_token = agents_env["regular_token"]
        factory = agents_env["factory"]

        # Admin creates an agent first
        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "locked"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        agent_id = resp.json()["id"]

        # Seed a room to try to add the agent into
        async with factory() as db:
            project = Project(name="p")
            db.add(project)
            await db.flush()
            room = Room(project_id=project.id, name="r")
            db.add(room)
            await db.commit()
            room_id = room.id

        # Regular user tries to add the agent to the room — must be 403
        resp = await client.post(
            f"/api/v1/agents/{agent_id}/rooms",
            json={"room_id": room_id},
            headers={"Authorization": f"Bearer {regular_token}"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_remove_agent_from_room_with_messages(self, agents_env) -> None:
        """Removing an agent from a room that has its messages must not
        raise IntegrityError. The messages should remain (participant_id NULL).
        """
        from doorae.db.models import Message, Participant, Project, Room

        client = agents_env["client"]
        admin_token = agents_env["token"]
        factory = agents_env["factory"]

        # Create an agent and assign it to a room, then drop a couple of
        # messages authored by that agent's participant.
        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "chatter"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        agent_id = resp.json()["id"]

        async with factory() as db:
            project = Project(name="p2")
            db.add(project)
            await db.flush()
            room = Room(project_id=project.id, name="r2")
            db.add(room)
            await db.commit()
            room_id = room.id

        resp = await client.post(
            f"/api/v1/agents/{agent_id}/rooms",
            json={"room_id": room_id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201

        # Find the participant row and write messages authored by it
        async with factory() as db:
            result = await db.execute(
                select(Participant).where(
                    Participant.agent_id == agent_id,
                    Participant.room_id == room_id,
                )
            )
            participant = result.scalar_one()
            for i in range(3):
                db.add(Message(
                    room_id=room_id,
                    participant_id=participant.id,
                    content=f"hello {i}",
                    seq=i + 1,
                ))
            await db.commit()
            participant_id = participant.id

        # Now remove the agent from the room — must succeed
        resp = await client.delete(
            f"/api/v1/agents/{agent_id}/rooms/{room_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, resp.text

        # Messages must still exist with participant_id set to NULL
        async with factory() as db:
            result = await db.execute(
                select(Message).where(Message.room_id == room_id)
            )
            remaining = list(result.scalars().all())
            assert len(remaining) == 3
            assert all(m.participant_id is None for m in remaining)

            # The participant record is gone
            result = await db.execute(
                select(Participant).where(Participant.id == participant_id)
            )
            assert result.scalar_one_or_none() is None

        # REST history endpoint must not 500 on orphan messages
        # (regression: Pydantic MessageOut used to require participant_id: str)
        # First add the admin as a room member so they can read
        async with factory() as db:
            db.add(Participant(
                room_id=room_id,
                user_id=agents_env["admin"].id,
                role="admin",
            ))
            await db.commit()

        resp = await client.get(
            f"/api/v1/rooms/{room_id}/messages?since_seq=0&limit=100",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 3
        assert all(m["participant_id"] is None for m in body)
        assert [m["content"] for m in body] == ["hello 0", "hello 1", "hello 2"]


    @pytest.mark.asyncio
    async def test_add_room_redispatches_pending_agent(self, agents_env) -> None:
        """Regression: an agent created with ``rooms=[]`` lands in
        ``pending`` because ``AgentLifecycle.request_start`` refuses
        to dispatch a roomless agent (prevents crash loops). Adding
        a room later MUST re-trigger the spawn attempt — otherwise
        the admin has to remember to click Start manually and the
        agent stays pending forever. Caught live in the 2026-04-12
        Playwright session with agents named "서브에이전트1" /
        "서브에이전트2" sitting at pending after room assignment.

        The failure mode of the un-fixed code path: ``add_agent_room``
        only re-dispatched for ``actual_state in (idle, stopped,
        crashed)`` — leaving ``pending`` in a silent dead-end.
        """
        from doorae.db.models import Project, Room

        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        # Create an agent — auto DM room means it starts immediately.
        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "roomless-first"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        agent_id = resp.json()["id"]

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            # DM room auto-created → agent is pending/running, not idle
            assert agent.desired_state == "running"

        # Seed a room the agent can be attached to.
        async with factory() as db:
            project = Project(name="late-rooms")
            db.add(project)
            await db.flush()
            room = Room(project_id=project.id, name="main")
            db.add(room)
            await db.commit()
            room_id = room.id

        # Add the agent to the freshly-created room. This call is the
        # one that used to leave the agent stuck at pending.
        resp = await client.post(
            f"/api/v1/agents/{agent_id}/rooms",
            json={"room_id": room_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text

        # Verify: the lifecycle was actually triggered. After a
        # successful dispatch the agent should have been placed on
        # the test machine and transitioned to ``pending`` (declarative
        # model: the machine hasn't confirmed yet).
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "pending", (
                f"expected pending after add_room dispatch, got {agent.actual_state!r}"
            )
            assert agent.placed_on_machine_id == agents_env["machine"].id, (
                "agent should be placed on the only machine in the test fixture"
            )

    @pytest.mark.asyncio
    async def test_add_room_bumps_generation_for_running_agent(
        self, agents_env
    ) -> None:
        """#227 — core fix. When an agent is already running and admin
        attaches it to a new room, the server must bump the agent's
        generation and re-send ``sync_desired_state`` so the machine
        re-spawns the subprocess with the refreshed ``--room`` argv.

        Before the fix this endpoint inserted the ``Participant`` row,
        skipped any WS notification, and then fell through to the
        dormant-state branch which only fired for ``idle``/``stopped``/
        ``crashed``/``pending`` — running agents got *nothing*. The
        in-flight agent process kept its original ``--room`` set and
        was invisible in the new room forever.
        """
        from doorae.db.models import Project, Room

        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]
        machine = agents_env["machine"]

        # Seed an agent already in steady-state running. The initial
        # room is needed so ``request_start`` (triggered once by the
        # generation bump inside our assertion path) wouldn't trip
        # the spawn_refused_no_rooms guard — but we're testing the
        # running branch so request_start never runs. Still, keep the
        # agent consistent with a real production row.
        async with factory() as db:
            project = Project(name="running-proj")
            db.add(project)
            await db.flush()
            seed_room = Room(project_id=project.id, name="seed")
            db.add(seed_room)
            await db.flush()
            agent = Agent(
                name="running-bot",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
                generation=5,
                pid=4321,
            )
            db.add(agent)
            await db.flush()
            db.add(Participant(
                room_id=seed_room.id, agent_id=agent.id, role="member"
            ))
            # Separate target room for the add-room call under test.
            target_room = Room(project_id=project.id, name="target")
            db.add(target_room)
            await db.commit()
            agent_id = agent.id
            target_room_id = target_room.id

        resp = await client.post(
            f"/api/v1/agents/{agent_id}/rooms",
            json={"room_id": target_room_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            # ``bump_generation`` bumped. ``request_start`` would have
            # set actual_state → pending; it must stay running since
            # we took the running branch.
            assert agent.generation == 6, (
                f"expected generation to bump from 5 to 6, got "
                f"{agent.generation!r}"
            )
            assert agent.actual_state == "running", (
                f"running agent must stay running across room-add; "
                f"got {agent.actual_state!r}"
            )
            # Participant row inserted via ensure_agent_in_room.
            part = (
                await db.execute(
                    select(Participant).where(
                        Participant.agent_id == agent_id,
                        Participant.room_id == target_room_id,
                    )
                )
            ).scalar_one_or_none()
            assert part is not None


class TestAgentActivityEndpoint:
    """GET /agents/{id}/activity surface (#222).

    ActivityPanel groups rows by ``request_id`` to render the per-turn
    timeline, so the endpoint must expose that column. Lifecycle events
    (message_received, handler_started, response_sent, handler_finished)
    carry a non-null ``request_id``; system events
    (start_requested / stop_requested) carry null.
    """

    @pytest.mark.asyncio
    async def test_activity_endpoint_exposes_request_id(
        self, agents_env
    ) -> None:
        from doorae.db.models import ActivityLog

        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        async with factory() as db:
            agent = Agent(
                name="activity-endpoint-agent",
                engine="echo",
                desired_state="stopped",
                actual_state="stopped",
            )
            db.add(agent)
            await db.flush()
            agent_id = agent.id
            # One lifecycle-style row (request_id set) and one system-
            # style row (request_id null) so the response can be
            # verified to preserve both shapes.
            db.add(ActivityLog(
                agent_id=agent_id,
                event_type="message_received",
                request_id="req-abc",
                details={"room_id": "room-x"},
            ))
            db.add(ActivityLog(
                agent_id=agent_id,
                event_type="start_requested",
                request_id=None,
                details=None,
            ))
            await db.commit()

        resp = await client.get(
            f"/api/v1/agents/{agent_id}/activity",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert {r["event_type"] for r in rows} == {
            "message_received",
            "start_requested",
        }
        by_type = {r["event_type"]: r for r in rows}
        assert by_type["message_received"]["request_id"] == "req-abc"
        assert by_type["start_requested"]["request_id"] is None


class TestAgentStopEndpoint:
    """POST /agents/{id}/stop response behaviour (#219).

    The machine page UX depends on the admin seeing the transition
    badge immediately after clicking Stop. The REST response and the
    next GET both need to surface ``actual_state='stopping'`` so the
    frontend's transitional-state poll loop has something to latch
    onto before the machine's next periodic report.
    """

    @pytest.mark.asyncio
    async def test_stop_sets_actual_state_stopping(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]
        machine = agents_env["machine"]

        # Seed a running agent placed on the fixture machine so the
        # orphan short-circuit path doesn't kick in.
        async with factory() as db:
            agent = Agent(
                name="stop-transition-agent",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
                pid=1234,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        resp = await client.post(
            f"/api/v1/agents/{agent_id}/stop",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["actual_state"] == "stopping"

        # Subsequent GET confirms the DB state was flipped.
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "stopping"
            assert agent.desired_state == "stopped"


class TestAgentManifestAPI:
    """Tests for the agents_md + agent_files editing surface.

    Option-B UI needs four things from the server:
      1. POST /agents can seed agents_md + files on create
      2. PUT /agents/{id} can update agents_md later (with explicit
         opt-in to set it to null)
      3. GET /agents/{id}/files lists what's on disk
      4. PUT /agents/{id}/files upserts a single file, DELETE removes it

    Every write goes through ``validate_agent_file_path`` — bad
    paths must 400 cleanly, not reach the DB and certainly not
    reach the materializer where they'd escape the agent root.
    """

    @pytest.mark.asyncio
    async def test_create_agent_with_manifest(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "with-manifest",
                "agents_md": "# agent\nBe helpful.",
                "files": {
                    "skills/greeting/SKILL.md": "---\nname: greeting\n---\nbody",
                    ".codex/config.toml": "[mcp_servers.docs]\ncommand = \"d\"\n",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["agents_md"] == "# agent\nBe helpful."
        agent_id = data["id"]

        # DB state matches the request manifest.
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.agents_md == "# agent\nBe helpful."

            files = (
                await db.execute(
                    select(AgentFile).where(AgentFile.agent_id == agent_id)
                )
            ).scalars().all()
            paths = {f.path: f.content for f in files}
            assert paths == {
                "skills/greeting/SKILL.md": "---\nname: greeting\n---\nbody",
                ".codex/config.toml": "[mcp_servers.docs]\ncommand = \"d\"\n",
            }

    @pytest.mark.asyncio
    async def test_create_agent_rejects_bad_file_path(self, agents_env) -> None:
        """A manifest with a path outside the whitelist must 400
        BEFORE any row lands in the DB — otherwise the next
        ``request_start`` would hand a bad path to the machine
        materializer, which would then reject the spawn and leave
        the agent stuck in ``pending``.
        """
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "bad-path",
                "files": {"../escape.md": "x"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "invalid file path" in resp.json()["detail"].lower()

        # Nothing should have been committed.
        async with factory() as db:
            agents = (
                await db.execute(select(Agent).where(Agent.name == "bad-path"))
            ).scalars().all()
            assert len(agents) == 0

    @pytest.mark.asyncio
    async def test_update_agent_rename_and_agents_md(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "before", "agents_md": "# v1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]
        assert resp.json()["agents_md"] == "# v1"
        assert resp.json()["name"] == "before"

        # Rename + bump agents_md
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "name": "after",
                "agents_md": "# v2",
                "agents_md_set": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "after"
        assert data["agents_md"] == "# v2"

    @pytest.mark.asyncio
    async def test_update_agent_clears_agents_md_with_explicit_flag(
        self, agents_env
    ) -> None:
        """Setting ``agents_md`` to ``None`` requires the explicit
        ``agents_md_set: True`` flag — otherwise we cannot tell
        "the caller omitted the field" from "the caller wants
        me to clear it". This test pins that distinction.
        """
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "clearable", "agents_md": "# v1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        # Without the flag: agents_md=null is IGNORED.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"agents_md": None},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["agents_md"] == "# v1"  # unchanged

        # With the flag: agents_md=null actually clears it.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"agents_md": None, "agents_md_set": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["agents_md"] is None

    @pytest.mark.asyncio
    async def test_update_agent_non_admin(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]
        regular_token = agents_env["regular_token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "guarded"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"name": "hijacked"},
            headers={"Authorization": f"Bearer {regular_token}"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_update_agent_404(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.put(
            "/api/v1/agents/does-not-exist",
            json={"name": "ghost"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_agent_avatar_roundtrip(self, agents_env) -> None:
        """Issue #101 — avatar_kind / avatar_value persist, and the
        ``*_set`` flags distinguish "omit" from "clear to null"."""
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "stylish"},
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()
        agent_id = data["id"]
        assert data["avatar_kind"] is None
        assert data["avatar_value"] is None

        # Set an emoji avatar.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "avatar_kind_set": True,
                "avatar_kind": "emoji",
                "avatar_value_set": True,
                "avatar_value": "🤖",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["avatar_kind"] == "emoji"
        assert resp.json()["avatar_value"] == "🤖"

        # Omit both — no change.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"name": "renamed"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["avatar_kind"] == "emoji"
        assert resp.json()["avatar_value"] == "🤖"

        # Reset back to initials with explicit flags.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "avatar_kind_set": True,
                "avatar_kind": None,
                "avatar_value_set": True,
                "avatar_value": None,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["avatar_kind"] is None
        assert resp.json()["avatar_value"] is None

    @pytest.mark.asyncio
    async def test_update_agent_avatar_only_does_not_bump_generation(
        self, agents_env
    ) -> None:
        """Issue #101 — avatar is pure UI metadata, so editing it
        alone must not trigger ``bump_generation`` (which respawns
        the agent). Mixed edits (avatar + another field) still bump
        as usual."""
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "stable"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            gen_before = agent.generation

        # Avatar-only change → no bump.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "avatar_kind_set": True,
                "avatar_kind": "lucide",
                "avatar_value_set": True,
                "avatar_value": "Rocket",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.generation == gen_before
            assert agent.avatar_kind == "lucide"
            assert agent.avatar_value == "Rocket"

        # Name + avatar → name side of the edit triggers the bump.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "name": "renamed",
                "avatar_kind_set": True,
                "avatar_kind": "emoji",
                "avatar_value_set": True,
                "avatar_value": "🧪",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.generation > gen_before
            assert agent.avatar_kind == "emoji"
            assert agent.avatar_value == "🧪"
            assert agent.name == "renamed"

    @pytest.mark.asyncio
    async def test_update_agent_context_window_opt_out_toggle(
        self, agents_env
    ) -> None:
        """#148 Part 2 — admin can toggle ``context_window_opt_out`` via PUT.

        Mirrors the other ``_set`` flags: omitting the flag keeps the
        previous value, supplying it persists whatever ``bool`` came
        in. The field also surfaces on GET/list so the admin UI can
        pre-select the current state.
        """
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        # Arrange — new agent defaults to opt_out=False.
        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "amb-opt"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["context_window_opt_out"] is False
        agent_id = resp.json()["id"]

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            gen_before = agent.generation

        # Act — opt out.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "context_window_opt_out": True,
                "context_window_opt_out_set": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["context_window_opt_out"] is True

        # Assert persisted + generation bumped (policy change requires
        # a respawn so the agent picks up the new setting).
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.context_window_opt_out is True
            assert agent.generation > gen_before

        # Rename without the _set flag must not reset the opt-out.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"name": "renamed", "context_window_opt_out": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "renamed"
        assert body["context_window_opt_out"] is True  # untouched

    @pytest.mark.asyncio
    async def test_update_agent_collaboration_mode_toggle(
        self, agents_env
    ) -> None:
        """#279 — admin can toggle ``collaboration_mode`` via PUT.

        Same ``_set`` flag pattern as #148: a rename PATCH without
        the flag must not reset the mode, and the field round-trips
        through GET. Treated as peer metadata (no generation bump).
        """
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        # Arrange — new agent defaults to ``solo``.
        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "collab-mode"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["collaboration_mode"] == "solo"
        agent_id = resp.json()["id"]

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            gen_before = agent.generation

        # Act — flip to collaborative.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "collaboration_mode": "collaborative",
                "collaboration_mode_set": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["collaboration_mode"] == "collaborative"

        # Assert persisted; peer metadata change → no generation bump.
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.collaboration_mode == "collaborative"
            assert agent.generation == gen_before

        # Rename without the _set flag must not reset the mode.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"name": "renamed", "collaboration_mode": "solo"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "renamed"
        assert body["collaboration_mode"] == "collaborative"

        # Invalid enum is rejected by Pydantic pattern.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "collaboration_mode": "telepath",
                "collaboration_mode_set": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_agent_with_collaboration_mode(self, agents_env) -> None:
        """#279 — POST accepts ``collaboration_mode`` and rejects garbage."""
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "born-collab",
                "collaboration_mode": "collaborative",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["collaboration_mode"] == "collaborative"

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "garbage-mode",
                "collaboration_mode": "argumentative",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_agent_with_description(self, agents_env) -> None:
        """#271 — POST accepts ``description`` and round-trips it on GET."""
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "intro-bot",
                "description": "Helpful research assistant",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["description"] == "Helpful research assistant"

        # And it persists on subsequent GET.
        resp = await client.get(
            f"/api/v1/agents/{body['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Helpful research assistant"

    @pytest.mark.asyncio
    async def test_create_agent_rejects_overlong_description(self, agents_env) -> None:
        """#271 — Pydantic enforces the 200-char cap so the LLM roster
        cost stays bounded."""
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "bad",
                "description": "x" * 201,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_agent_description_roundtrip_and_clear(
        self, agents_env
    ) -> None:
        """#271 — PATCH semantics:

        * supplying ``description_set=True`` with text persists it
        * supplying ``description_set=True`` with ``None`` clears it
        * omitting the ``_set`` flag never touches the stored value
        * description-only edits are peer metadata: no generation bump
        """
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "intro2"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]
        assert resp.json()["description"] is None

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            gen_before = agent.generation

        # Set.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"description": "Frontend reviewer", "description_set": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Frontend reviewer"

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.description == "Frontend reviewer"
            # Description is peer metadata — must not bump generation.
            assert agent.generation == gen_before

        # Omitting the _set flag leaves the field untouched.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"description": None},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Frontend reviewer"

        # Explicit clear.
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={"description": None, "description_set": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] is None

    @pytest.mark.asyncio
    async def test_list_agent_files_empty(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "empty-manifest"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/agents/{agent_id}/files",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_agent_files_sorted(self, agents_env) -> None:
        """List endpoint must return rows in path-sorted order so
        the UI gets a deterministic tree regardless of insert
        order. Server-side sort avoids pushing that onto every
        client.
        """
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "sorted",
                "files": {
                    "skills/zzz/SKILL.md": "z",
                    "skills/aaa/SKILL.md": "a",
                    ".codex/config.toml": "c",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/agents/{agent_id}/files",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        paths = [f["path"] for f in resp.json()]
        assert paths == [
            ".codex/config.toml",
            "skills/aaa/SKILL.md",
            "skills/zzz/SKILL.md",
        ]

    @pytest.mark.asyncio
    async def test_upsert_agent_file_create(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "upsert-create"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        resp = await client.put(
            f"/api/v1/agents/{agent_id}/files",
            json={
                "path": "skills/coder/SKILL.md",
                "content": "---\nname: coder\n---\nbody",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["path"] == "skills/coder/SKILL.md"
        assert body["content"] == "---\nname: coder\n---\nbody"

    @pytest.mark.asyncio
    async def test_upsert_agent_file_update_existing(self, agents_env) -> None:
        """Sending PUT twice to the same path replaces the content
        (it's an upsert keyed on ``(agent_id, path)``). Without
        upsert semantics the second PUT would 409 on the unique
        constraint.
        """
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "upsert-update",
                "files": {"skills/coder/SKILL.md": "v1"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        resp = await client.put(
            f"/api/v1/agents/{agent_id}/files",
            json={"path": "skills/coder/SKILL.md", "content": "v2"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "v2"

        # Confirm the list endpoint sees the updated bytes, not both.
        resp = await client.get(
            f"/api/v1/agents/{agent_id}/files",
            headers={"Authorization": f"Bearer {token}"},
        )
        files = resp.json()
        assert len(files) == 1
        assert files[0]["content"] == "v2"

    @pytest.mark.asyncio
    async def test_upsert_agent_file_rejects_bad_path(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "bad-upsert"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        resp = await client.put(
            f"/api/v1/agents/{agent_id}/files",
            json={"path": "workspace/evil.md", "content": "x"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "workspace" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_delete_agent_file(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "del-file",
                "files": {"skills/coder/SKILL.md": "c"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        resp = await client.request(
            "DELETE",
            f"/api/v1/agents/{agent_id}/files",
            json={"path": "skills/coder/SKILL.md"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "deleted": True,
            "path": "skills/coder/SKILL.md",
        }

        # The list endpoint now returns an empty manifest.
        resp = await client.get(
            f"/api/v1/agents/{agent_id}/files",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_delete_agent_file_404(self, agents_env) -> None:
        client = agents_env["client"]
        token = agents_env["token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "del-missing"},
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        resp = await client.request(
            "DELETE",
            f"/api/v1/agents/{agent_id}/files",
            json={"path": "skills/coder/SKILL.md"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_non_admin_cannot_list_files(self, agents_env) -> None:
        client = agents_env["client"]
        admin_token = agents_env["token"]
        regular_token = agents_env["regular_token"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "guarded-files"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        agent_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/agents/{agent_id}/files",
            headers={"Authorization": f"Bearer {regular_token}"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_agent_delete_cascades_files(self, agents_env) -> None:
        """Dropping an agent must also drop its AgentFile rows. The
        FK has ``ondelete=CASCADE`` on the model; this test makes
        sure the REST DELETE flow actually triggers it.
        """
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        resp = await client.post(
            "/api/v1/agents",
            json={
                "engine": "echo",
                "name": "cascade",
                "files": {"skills/coder/SKILL.md": "c"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        agent_id = resp.json()["id"]

        async with factory() as db:
            rows = (
                await db.execute(
                    select(AgentFile).where(AgentFile.agent_id == agent_id)
                )
            ).scalars().all()
            assert len(rows) == 1

        resp = await client.delete(
            f"/api/v1/agents/{agent_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        async with factory() as db:
            rows = (
                await db.execute(
                    select(AgentFile).where(AgentFile.agent_id == agent_id)
                )
            ).scalars().all()
            assert rows == []


class TestAgentAutoDM:
    """Tests for automatic DM room creation on agent creation."""

    @pytest.mark.asyncio
    async def test_create_agent_creates_dm_room(self, agents_env) -> None:
        """Agent creation with no rooms still creates a DM room."""
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "dm-agent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        agent_id = resp.json()["id"]

        # Verify DM room was created
        async with factory() as db:
            dm_rooms = (
                await db.execute(
                    select(Room).where(Room.is_dm == True, Room.name == "DM: dm-agent")
                )
            ).scalars().all()
            assert len(dm_rooms) == 1
            dm = dm_rooms[0]

            # Agent is a participant of the DM room
            agent_part = (
                await db.execute(
                    select(Participant).where(
                        Participant.room_id == dm.id,
                        Participant.agent_id == agent_id,
                    )
                )
            ).scalar_one_or_none()
            assert agent_part is not None

    @pytest.mark.asyncio
    async def test_create_agent_with_rooms_also_has_dm(self, agents_env) -> None:
        """Agent created with explicit rooms also gets a DM room."""
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        # Get project_id from fixture
        async with factory() as db:
            project = (await db.execute(select(Project).limit(1))).scalar_one()
            project_id = project.id

        room_resp = await client.post(
            "/api/v1/rooms",
            json={"project_id": project_id, "name": "extra-room"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert room_resp.status_code == 201
        room_id = room_resp.json()["id"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "multi-room-agent", "rooms": [room_id]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        agent_id = resp.json()["id"]

        # Agent should have 2 rooms: the explicit room + the DM
        async with factory() as db:
            parts = (
                await db.execute(
                    select(Participant.room_id).where(Participant.agent_id == agent_id)
                )
            ).scalars().all()
            assert len(parts) == 2
            assert room_id in parts

    @pytest.mark.asyncio
    async def test_agent_dm_has_null_project_id(self, agents_env) -> None:
        """#179 — DM rooms must be decoupled from projects.

        Creating an agent auto-creates its DM with ``project_id=NULL`` so the
        DM survives when any project is deleted. Previously the DM inherited
        the oldest project's id and got cascade-deleted alongside it.
        """
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "null-proj-agent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        async with factory() as db:
            dm = (
                await db.execute(
                    select(Room).where(
                        Room.is_dm == True,  # noqa: E712
                        Room.name == "DM: null-proj-agent",
                    )
                )
            ).scalar_one()
            assert dm.project_id is None

    @pytest.mark.asyncio
    async def test_project_delete_preserves_dm(self, agents_env) -> None:
        """#179 — Deleting a project must not cascade-delete agent DM rooms.

        The fixture seeds exactly one project. We create an agent (which
        auto-creates its DM), then delete the seeded project. The DM must
        still exist afterwards — the whole point of decoupling.
        """
        client = agents_env["client"]
        token = agents_env["token"]
        factory = agents_env["factory"]

        resp = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "survivor-agent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

        # Snapshot the DM id so we can check it after the project purge.
        async with factory() as db:
            dm_id = (
                await db.execute(
                    select(Room.id).where(
                        Room.is_dm == True,  # noqa: E712
                        Room.name == "DM: survivor-agent",
                    )
                )
            ).scalar_one()

            project_id = (
                await db.execute(select(Project.id).limit(1))
            ).scalar_one()

        # Wipe the only project. Under the old (buggy) behaviour this
        # would also cascade the DM away.
        resp = await client.delete(
            f"/api/v1/projects/{project_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204

        async with factory() as db:
            survivor = (
                await db.execute(select(Room).where(Room.id == dm_id))
            ).scalar_one_or_none()
            assert survivor is not None, "DM room was cascade-deleted with the project"
            assert survivor.project_id is None


class TestAgentPermissionLevel:
    """#309 — per-agent permission tier admin PATCH + audit log."""

    @pytest_asyncio.fixture
    async def agent_id(self, agents_env) -> str:
        """Create a single agent and return its id. Reused across the
        permission-level tests so they don't each pay the create cost."""
        resp = await agents_env["client"].post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "perm-agent"},
            headers={"Authorization": f"Bearer {agents_env['token']}"},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    @pytest.mark.asyncio
    async def test_default_permission_level_is_null(
        self, agents_env, agent_id: str
    ) -> None:
        """Newly created agents have ``permission_level=None`` so the
        adapter falls back to the standard tier (= pre-#309 hardcoded
        behaviour). Confirms the migration's NULL default round-trips
        through ``GET /agents/{id}``."""
        resp = await agents_env["client"].get(
            f"/api/v1/agents/{agent_id}",
            headers={"Authorization": f"Bearer {agents_env['token']}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "permission_level" in body
        assert body["permission_level"] is None

    @pytest.mark.asyncio
    async def test_admin_can_set_permission_level(
        self, agents_env, agent_id: str
    ) -> None:
        client = agents_env["client"]
        token = agents_env["token"]
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "permission_level": "trusted",
                "permission_level_set": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["permission_level"] == "trusted"

    @pytest.mark.asyncio
    async def test_admin_can_clear_permission_level(
        self, agents_env, agent_id: str
    ) -> None:
        """Sending ``permission_level=null, _set=true`` clears the
        field — the same explicit-clear contract as ``description`` /
        ``memory_md`` etc."""
        client = agents_env["client"]
        token = agents_env["token"]
        # Set, then clear.
        await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "permission_level": "restricted",
                "permission_level_set": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "permission_level": None,
                "permission_level_set": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["permission_level"] is None

    @pytest.mark.asyncio
    async def test_invalid_permission_level_rejected(
        self, agents_env, agent_id: str
    ) -> None:
        """Pydantic pattern guard — only restricted/standard/trusted
        and ``null`` are accepted."""
        resp = await agents_env["client"].put(
            f"/api/v1/agents/{agent_id}",
            json={
                "permission_level": "godmode",
                "permission_level_set": True,
            },
            headers={"Authorization": f"Bearer {agents_env['token']}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_non_admin_cannot_set_permission_level(
        self, agents_env, agent_id: str
    ) -> None:
        """The PATCH endpoint is admin-only at the dependency layer
        (``get_admin_identity``). Regular users get 403 regardless of
        which field they try to flip."""
        resp = await agents_env["client"].put(
            f"/api/v1/agents/{agent_id}",
            json={
                "permission_level": "trusted",
                "permission_level_set": True,
            },
            headers={
                "Authorization": f"Bearer {agents_env['regular_token']}"
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_permission_change_writes_activity_log(
        self, agents_env, agent_id: str
    ) -> None:
        """Audit trail — every transition lands in ``activity_logs``
        with ``event_type='agent_permission_changed'`` and the
        from/to/by_user_id details so security review can reconstruct
        who flipped what when."""
        await agents_env["client"].put(
            f"/api/v1/agents/{agent_id}",
            json={
                "permission_level": "trusted",
                "permission_level_set": True,
            },
            headers={"Authorization": f"Bearer {agents_env['token']}"},
        )
        async with agents_env["factory"]() as db:
            row = (
                await db.execute(
                    select(ActivityLog).where(
                        ActivityLog.agent_id == agent_id,
                        ActivityLog.event_type == "agent_permission_changed",
                    )
                )
            ).scalar_one()
        assert row.details is not None
        assert row.details["from"] is None
        assert row.details["to"] == "trusted"
        assert row.details["by_user_id"] == agents_env["admin"].id

    @pytest.mark.asyncio
    async def test_no_audit_when_value_unchanged(
        self, agents_env, agent_id: str
    ) -> None:
        """Re-PATCHing the *same* value is a no-op for the audit log —
        otherwise repeated saves from the UI would multiply rows."""
        client = agents_env["client"]
        token = agents_env["token"]
        await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "permission_level": "standard",
                "permission_level_set": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "permission_level": "standard",
                "permission_level_set": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        async with agents_env["factory"]() as db:
            count = len(
                (
                    await db.execute(
                        select(ActivityLog).where(
                            ActivityLog.agent_id == agent_id,
                            ActivityLog.event_type == "agent_permission_changed",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert count == 1
