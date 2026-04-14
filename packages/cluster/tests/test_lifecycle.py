"""Tests for agent lifecycle state transitions."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
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


class FakeWS:
    """Captures sent frames for assertion."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


@pytest_asyncio.fixture()
async def lifecycle_env():
    """Set up DB, bus, lifecycle, and a machine with an engine."""
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    fake_ws = FakeWS()

    async with factory() as db:
        user = User(email="lc@test.com", password_hash="x")
        db.add(user)
        await db.flush()

        machine = Machine(
            name="lc-machine",
            hostname="host-lc",
            owner_user_id=user.id,
            status="online",
            max_agents=5,
        )
        db.add(machine)
        await db.flush()

        db.add(MachineEngine(machine_id=machine.id, engine="echo"))

        project = Project(name="lc-project")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="lc-room")
        db.add(room)
        await db.commit()

        await bus.register(machine.id, fake_ws)

        room_id = room.id

    async def attach_to_room(agent_id: str) -> None:
        """Make *agent_id* a participant of the default test room."""
        async with factory() as db:
            db.add(Participant(room_id=room_id, agent_id=agent_id, role="member"))
            await db.commit()

    yield {
        "factory": factory,
        "bus": bus,
        "lifecycle": lifecycle,
        "machine": machine,
        "fake_ws": fake_ws,
        "user": user,
        "room_id": room_id,
        "attach_to_room": attach_to_room,
    }

    await engine.dispose()


class TestAgentLifecycle:
    @pytest.mark.asyncio
    async def test_pending_to_pending_to_running(self, lifecycle_env) -> None:
        """request_start sends sync_desired_state (pending), then
        handle_report_actual_state transitions to running."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-1",
                engine="echo",
                desired_state="running",
                actual_state="pending",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)

        # request_start sends sync_desired_state; actual_state stays "pending"
        await lifecycle.request_start(agent_id)

        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one()
            assert agent.actual_state == "pending"
            assert agent.placed_on_machine_id is not None

        # Machine reports running (simulating the agent started successfully)
        await lifecycle.handle_report_actual_state(
            machine.id,
            [{"agent_id": agent_id, "actual_state": "running", "pid": 1234}],
        )

        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one()
            assert agent.actual_state == "running"
            assert agent.pid == 1234

    @pytest.mark.asyncio
    async def test_running_to_crashed_restart_anywhere(self, lifecycle_env) -> None:
        """Agent crash with restart_anywhere policy: machine requests
        replacement, which re-places the agent and sends sync_desired_state."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-crash",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
                pid=5555,
                restart_policy="restart_anywhere",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)

        # Machine requests replacement (crash + restart_anywhere → re-place)
        await lifecycle.handle_request_replacement(
            machine.id, agent_id, reason="segfault"
        )

        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one()
            # After replacement request + request_start, should be "pending"
            assert agent.actual_state == "pending"
            assert agent.last_crash_reason is not None
            # The reason recorded before re-placement
            assert "segfault" in agent.last_crash_reason

    @pytest.mark.asyncio
    async def test_crashed_with_stop_policy(self, lifecycle_env) -> None:
        """Agent crash with 'stop' policy: machine reports stopped and
        the server honours it by keeping desired_state consistent."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-stop",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
                pid=6666,
                restart_policy="stop",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        # Machine reports that the agent has stopped (no restart)
        await lifecycle.handle_report_actual_state(
            machine.id,
            [
                {
                    "agent_id": agent_id,
                    "actual_state": "stopped",
                    "last_crash_reason": "err",
                }
            ],
        )

        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one()
            assert agent.actual_state == "stopped"

    @pytest.mark.asyncio
    async def test_request_stop_sends_sync_desired_state(self, lifecycle_env) -> None:
        """request_stop sends sync_desired_state(desired='stopped') to the machine."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]
        fake_ws = lifecycle_env["fake_ws"]

        async with factory() as db:
            agent = Agent(
                name="agent-kill",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
                pid=7777,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        initial_sent_count = len(fake_ws.sent)
        await lifecycle.request_stop(agent_id)

        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one()
            assert agent.desired_state == "stopped"

        # A sync_desired_state frame with desired_state="stopped" should have been sent
        assert len(fake_ws.sent) > initial_sent_count
        frame = json.loads(fake_ws.sent[-1])
        assert frame["type"] == "sync_desired_state"
        assert frame["desired_state"] == "stopped"

    @pytest.mark.asyncio
    async def test_request_start_refuses_when_no_rooms(self, lifecycle_env) -> None:
        """Agents with zero room memberships must not be handed to the daemon.

        Without ``--room`` the agent subprocess crashes on boot, and with the
        default ``restart_anywhere`` policy that turns into an infinite loop
        of token creation + spawn attempts. Guard at the lifecycle level so
        every caller (create_agent, restart-on-crash, manual start) is safe.
        """
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        fake_ws = lifecycle_env["fake_ws"]

        async with factory() as db:
            agent = Agent(
                name="agent-roomless",
                engine="echo",
                desired_state="running",
                actual_state="pending",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        sent_before = len(fake_ws.sent)
        await lifecycle.request_start(agent_id)

        # No sync_desired_state frame should have reached the machine.
        assert len(fake_ws.sent) == sent_before

        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one()
            # State stays pending so the admin sees "not running" rather
            # than a phantom "starting" that will never resolve.
            assert agent.actual_state == "pending"
            # No machine placement since we refused to dispatch.
            assert agent.placed_on_machine_id is None
            # A human-readable reason should be recorded.
            assert agent.last_crash_reason is not None
            assert "room" in agent.last_crash_reason.lower()

    @pytest.mark.asyncio
    async def test_request_start_ships_manifest_to_daemon(
        self, lifecycle_env
    ) -> None:
        """When the agent has an AGENTS.md body and agent_files rows,
        request_start must send a sync_desired_state frame containing them
        so the machine can materialize the per-agent directory."""
        import json

        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        fake_ws = lifecycle_env["fake_ws"]

        async with factory() as db:
            agent = Agent(
                name="agent-manifest",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                agents_md="# Agent\nYou are a test agent.",
            )
            db.add(agent)
            await db.flush()
            db.add(
                AgentFile(
                    agent_id=agent.id,
                    path="skills/coder/SKILL.md",
                    content="---\nname: coder\ndescription: Writes code\n---\nbody",
                )
            )
            db.add(
                AgentFile(
                    agent_id=agent.id,
                    path=".codex/config.toml",
                    content='[mcp_servers.docs]\ncommand = "docs-mcp"\n',
                )
            )
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)

        sent_before = len(fake_ws.sent)
        await lifecycle.request_start(agent_id)
        assert len(fake_ws.sent) > sent_before

        frame = json.loads(fake_ws.sent[-1])
        assert frame["type"] == "sync_desired_state"
        assert frame["agent_id"] == agent_id
        assert frame["agents_md"] == "# Agent\nYou are a test agent."
        assert frame["files"] == {
            "skills/coder/SKILL.md": "---\nname: coder\ndescription: Writes code\n---\nbody",
            ".codex/config.toml": '[mcp_servers.docs]\ncommand = "docs-mcp"\n',
        }
        assert frame["engine_secrets"] == {}

    @pytest.mark.asyncio
    async def test_request_start_legacy_agent_no_manifest(
        self, lifecycle_env
    ) -> None:
        """Agents that have no agents_md and no agent_files rows still
        dispatch successfully via the legacy profile_yaml path. The
        sync_desired_state frame carries agents_md=None and files={}, and the
        machine-side materializer treats that as "nothing to drop on
        disk, fall back to profile_yaml".
        """
        import json

        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        fake_ws = lifecycle_env["fake_ws"]

        async with factory() as db:
            agent = Agent(
                name="agent-legacy",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                profile_yaml="name: agent-legacy\nmodel: gpt\n",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)
        await lifecycle.request_start(agent_id)

        frame = json.loads(fake_ws.sent[-1])
        assert frame["type"] == "sync_desired_state"
        assert frame["agents_md"] is None
        assert frame["files"] == {}
        assert frame["profile_yaml"] == "name: agent-legacy\nmodel: gpt\n"

    @pytest.mark.asyncio
    async def test_on_agent_stopped(self, lifecycle_env) -> None:
        """handle_report_actual_state with actual_state='stopped' transitions
        the agent to 'stopped'."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-stopped",
                engine="echo",
                desired_state="stopped",
                actual_state="running",
                placed_on_machine_id=machine.id,
                pid=8888,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle.handle_report_actual_state(
            machine.id,
            [{"agent_id": agent_id, "actual_state": "stopped", "pid": None}],
        )

        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one()
            assert agent.actual_state == "stopped"
            assert agent.pid is None
