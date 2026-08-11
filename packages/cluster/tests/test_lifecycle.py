"""Tests for agent lifecycle state transitions."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.agent_availability import SPAWN_FAILED
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    ActivityLog,
    Agent,
    AgentFile,
    AgentToken,
    Base,
    Machine,
    MachineEngine,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.scheduler.lifecycle import (
    GENERATION_REPORT_CAPABILITY,
    AgentLifecycle,
    sweep_stale_agents,
)
from anygarden.scheduler.machine_bus import MachineBus
from anygarden_machine.daemon import MachineDaemon


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
            assert agent.lifecycle_lease_token is not None
            assert agent.lifecycle_delivery_state == "pending_ack"

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
            assert agent.lifecycle_lease_token is None
            assert agent.lifecycle_delivery_state == "acknowledged"

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
            assert agent.generation == 1

        # A sync_desired_state frame with desired_state="stopped" should have been sent
        assert len(fake_ws.sent) > initial_sent_count
        frame = json.loads(fake_ws.sent[-1])
        assert frame["type"] == "sync_desired_state"
        assert frame["desired_state"] == "stopped"
        assert frame["generation"] == 1

    # ── #219: stopping transitional state ─────────────────────────

    @pytest.mark.asyncio
    async def test_request_stop_marks_actual_state_stopping(
        self, lifecycle_env
    ) -> None:
        """request_stop must flip actual_state from 'running' to
        'stopping' immediately so admins see the transition without
        waiting for the machine's next periodic report (#219)."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-stop-transitional",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
                pid=8000,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle.request_stop(agent_id)

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.desired_state == "stopped"
            assert agent.actual_state == "stopping"

    @pytest.mark.asyncio
    async def test_request_stop_from_starting_goes_to_stopping(
        self, lifecycle_env
    ) -> None:
        """Admin aborts a slow spawn: starting → stopping, not stuck starting."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-abort-start",
                engine="echo",
                desired_state="running",
                actual_state="starting",
                placed_on_machine_id=machine.id,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle.request_stop(agent_id)

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "stopping"

    @pytest.mark.asyncio
    async def test_request_stop_unplaced_goes_to_stopped(
        self, lifecycle_env
    ) -> None:
        """An agent with no machine assigned has no daemon to tell —
        the absent-from-report convergence loop never runs for it, so
        'stopping' would leak forever. Short-circuit to 'stopped'."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]

        async with factory() as db:
            agent = Agent(
                name="agent-orphan",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                placed_on_machine_id=None,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle.request_stop(agent_id)

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "stopped"
            assert agent.desired_state == "stopped"

    @pytest.mark.asyncio
    async def test_stopping_converges_to_stopped_via_absent_report(
        self, lifecycle_env
    ) -> None:
        """Once the machine drops the agent from its next report,
        handle_report_actual_state's absent-from-report branch converges
        actual_state='stopping' to 'stopped' — the normal exit path for
        #219's transitional state."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-converge",
                engine="echo",
                desired_state="stopped",
                actual_state="stopping",
                placed_on_machine_id=machine.id,
                pid=1,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        # Machine sends a report that no longer mentions this agent.
        await lifecycle.handle_report_actual_state(machine.id, [])

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "stopped"
            assert agent.pid is None

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
    async def test_request_start_preserves_last_crash_diagnostic(
        self, lifecycle_env
    ) -> None:
        """A retry keeps prior diagnostics; UI state prevents stale alarms."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]

        async with factory() as db:
            agent = Agent(
                name="agent-stale-warning",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                last_crash_reason="stale bootstrap failure",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)
        await lifecycle.request_start(agent_id)

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.last_crash_reason == "stale bootstrap failure"

    @pytest.mark.asyncio
    async def test_request_start_send_failure_preserves_placement_and_marks_unknown(
        self, lifecycle_env, monkeypatch
    ) -> None:
        """A False send result is unknown delivery, not proof of nondelivery."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        bus = lifecycle_env["bus"]
        machine = lifecycle_env["machine"]

        async def fail_send(machine_id: str, frame: dict) -> bool:
            return False

        monkeypatch.setattr(bus, "send", fail_send)

        async with factory() as db:
            agent = Agent(
                name="agent-send-fail",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                last_crash_reason="stale bootstrap failure",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)
        await lifecycle.request_start(agent_id)

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.placed_on_machine_id == machine.id
            assert agent.generation == 1
            assert agent.lifecycle_lease_token is not None
            assert agent.lifecycle_delivery_state == "unknown"
            assert agent.unavailable_code == SPAWN_FAILED
            assert agent.unavailable_detail == {
                "machine_id": machine.id,
                "reason": "delivery_unknown",
            }
            assert "dispatch_unknown to" in (agent.last_crash_reason or "")

            activity = (
                (
                    await db.execute(
                        select(ActivityLog).where(
                            ActivityLog.agent_id == agent_id,
                            ActivityLog.event_type == "lifecycle_dispatch_unknown",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(activity) == 1
            assert activity[0].details["generation"] == 1
            assert activity[0].details["recovery"] == "same_placement_reconcile"

    @pytest.mark.asyncio
    async def test_request_start_concurrent_calls_send_once(self, lifecycle_env) -> None:
        """Concurrent in-process start calls are coalesced."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        fake_ws = lifecycle_env["fake_ws"]

        async with factory() as db:
            agent = Agent(
                name="agent-race",
                engine="echo",
                desired_state="running",
                actual_state="pending",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)
        initial = len(fake_ws.sent)

        await asyncio.gather(
            lifecycle.request_start(agent_id),
            lifecycle.request_start(agent_id),
        )

        assert len(fake_ws.sent) == initial + 1
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.generation == 1

    @pytest.mark.asyncio
    async def test_request_start_concurrent_workers_share_durable_claim(
        self, lifecycle_env
    ) -> None:
        """Separate lifecycle instances still produce one generation/dispatch."""
        factory = lifecycle_env["factory"]
        bus = lifecycle_env["bus"]
        first = lifecycle_env["lifecycle"]
        second = AgentLifecycle(db_factory=factory, machine_bus=bus)
        fake_ws = lifecycle_env["fake_ws"]

        async with factory() as db:
            agent = Agent(
                name="agent-cross-worker-race",
                engine="echo",
                desired_state="running",
                actual_state="pending",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)
        initial = len(fake_ws.sent)
        await asyncio.gather(
            first.request_start(agent_id),
            second.request_start(agent_id),
        )

        assert len(fake_ws.sent) == initial + 1
        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.generation == 1
            assert agent.lifecycle_lease_token is not None
            assert agent.lifecycle_delivery_state == "pending_ack"

    @pytest.mark.asyncio
    async def test_uncertain_delivery_recovers_same_generation_and_fences_old_owner(
        self, lifecycle_env, monkeypatch
    ) -> None:
        """An expired uncertain claim retries only its placement/generation."""
        factory = lifecycle_env["factory"]
        bus = lifecycle_env["bus"]
        machine = lifecycle_env["machine"]
        first = lifecycle_env["lifecycle"]
        second = AgentLifecycle(db_factory=factory, machine_bus=bus)
        delivery_results = iter((False, True))
        frames: list[dict] = []

        async def sequenced_send(machine_id: str, frame: dict) -> bool:
            frames.append(frame)
            return next(delivery_results)

        monkeypatch.setattr(bus, "send", sequenced_send)
        async with factory() as db:
            agent = Agent(
                name="agent-uncertain-recovery",
                engine="echo",
                desired_state="running",
                actual_state="pending",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)
        await first.request_start(agent_id)
        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            old_token = agent.lifecycle_lease_token
            assert old_token is not None
            assert agent.lifecycle_delivery_state == "unknown"
            assert agent.placed_on_machine_id == machine.id
            assert agent.generation == 1
            agent.lifecycle_lease_expires_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            await db.commit()

        await second.request_start(agent_id)
        assert [frame["generation"] for frame in frames] == [1, 1]

        stale_applied = await first._record_dispatch_result(
            agent_id=agent_id,
            generation=1,
            machine_id=machine.id,
            ownership_token=old_token,
            sent=False,
        )
        assert stale_applied is False

        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.placed_on_machine_id == machine.id
            assert agent.generation == 1
            assert agent.lifecycle_lease_token != old_token
            assert agent.lifecycle_delivery_state == "pending_ack"

    @pytest.mark.asyncio
    async def test_stale_send_failure_cannot_clobber_stop_start_generation(
        self, lifecycle_env, monkeypatch
    ) -> None:
        """A delayed result from generation N is fenced after stop N+1/start N+2."""
        factory = lifecycle_env["factory"]
        bus = lifecycle_env["bus"]
        machine = lifecycle_env["machine"]
        first = lifecycle_env["lifecycle"]
        second = AgentLifecycle(db_factory=factory, machine_bus=bus)
        first_send_entered = asyncio.Event()
        release_first_send = asyncio.Event()
        sent_frames: list[dict] = []

        async def delayed_first_send(machine_id: str, frame: dict) -> bool:
            sent_frames.append(frame)
            if len(sent_frames) == 1:
                first_send_entered.set()
                await release_first_send.wait()
                return False
            return True

        monkeypatch.setattr(bus, "send", delayed_first_send)
        async with factory() as db:
            agent = Agent(
                name="agent-stale-dispatch-result",
                engine="echo",
                desired_state="running",
                actual_state="pending",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)
        first_start = asyncio.create_task(first.request_start(agent_id))
        await first_send_entered.wait()

        await second.request_stop(agent_id)
        await second.request_start(agent_id)
        release_first_send.set()
        await first_start

        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.desired_state == "running"
            assert agent.actual_state == "pending"
            assert agent.placed_on_machine_id == machine.id
            assert agent.generation == 3
            assert agent.lifecycle_delivery_state == "pending_ack"
            assert agent.unavailable_code is None

        assert [frame["generation"] for frame in sent_frames] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_delayed_running_dispatch_cannot_reanimate_after_stop(
        self, lifecycle_env, monkeypatch, tmp_path
    ) -> None:
        """Actual wire order start N → stop N+1 → delayed running N is fenced."""
        factory = lifecycle_env["factory"]
        bus = lifecycle_env["bus"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]
        daemon = MachineDaemon(
            server_url="wss://cluster.test/ws/machines/lc-machine",
            machine_id=machine.id,
            machine_token="machine-token",
            agent_dirs_root=tmp_path / "agents",
            workspace_registry_path=tmp_path / "workspaces.json",
            workspace_signing_key_path=tmp_path / "workspace-signing.key",
        )
        daemon._request_token_and_spawn = AsyncMock()  # type: ignore[method-assign]
        first_running_entered = asyncio.Event()
        release_first_running = asyncio.Event()
        delivered: list[tuple[str, int]] = []

        async def delayed_wire_send(machine_id: str, frame: dict) -> bool:
            assert machine_id == machine.id
            if frame["desired_state"] == "running":
                first_running_entered.set()
                await release_first_running.wait()
            delivered.append((frame["desired_state"], frame["generation"]))
            await daemon._handle(frame)
            return True

        monkeypatch.setattr(bus, "send", delayed_wire_send)
        async with factory() as db:
            agent = Agent(
                name="agent-wire-stop-fence",
                engine="echo",
                desired_state="running",
                actual_state="pending",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id
        await lifecycle_env["attach_to_room"](agent_id)

        start_task = asyncio.create_task(lifecycle.request_start(agent_id))
        await first_running_entered.wait()
        await lifecycle.request_stop(agent_id)
        release_first_running.set()
        await start_task

        # The daemon's next full report confirms that no process survived.
        await lifecycle.handle_report_actual_state(machine.id, [])

        assert delivered == [("stopped", 2), ("running", 1)]
        daemon._request_token_and_spawn.assert_not_awaited()
        manifest = daemon._manifest_store.load(agent_id)
        assert manifest is not None
        assert manifest.desired_state == "stopped"
        assert manifest.generation == 2
        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.desired_state == "stopped"
            assert agent.actual_state == "stopped"
            assert agent.generation == 2

        # A fresh daemon sharing the manifest disk enforces the same fence.
        restarted = MachineDaemon(
            server_url=daemon.server_url,
            machine_id=daemon.machine_id,
            machine_token=daemon.machine_token,
            agent_dirs_root=tmp_path / "agents",
            workspace_registry_path=tmp_path / "restart-workspaces.json",
            workspace_signing_key_path=tmp_path / "restart-signing.key",
        )
        restarted._request_token_and_spawn = AsyncMock()  # type: ignore[method-assign]
        await restarted._handle(
            {
                "type": "sync_desired_state",
                "agent_id": agent_id,
                "desired_state": "running",
                "generation": 1,
                "engine": "echo",
            }
        )
        restarted._request_token_and_spawn.assert_not_awaited()
        persisted = restarted._manifest_store.load(agent_id)
        assert persisted is not None
        assert persisted.desired_state == "stopped"
        assert persisted.generation == 2

    @pytest.mark.asyncio
    async def test_unversioned_report_is_fenced_after_legacy_epoch(
        self, lifecycle_env
    ) -> None:
        """Legacy reports work for one epoch, then require a generation."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-legacy-report-epoch",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                placed_on_machine_id=machine.id,
                generation=1,
                legacy_report_generation=1,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle.handle_report_actual_state(
            machine.id,
            [{"agent_id": agent_id, "actual_state": "running", "pid": 101}],
        )
        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.actual_state == "running"
            agent.actual_state = "pending"
            agent.pid = None
            agent.generation = 2
            await db.commit()

        await lifecycle.handle_report_actual_state(
            machine.id,
            [{"agent_id": agent_id, "actual_state": "running", "pid": 202}],
        )
        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.actual_state == "pending"
            assert agent.pid is None

        await lifecycle.handle_report_actual_state(
            machine.id,
            [
                {
                    "agent_id": agent_id,
                    "actual_state": "running",
                    "generation": 2,
                    "pid": 202,
                }
            ],
        )
        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.actual_state == "running"
            assert agent.pid == 202

    @pytest.mark.asyncio
    async def test_generation_capable_machine_rejects_unversioned_report(
        self, lifecycle_env
    ) -> None:
        """Capability advertisement closes the unversioned-report path."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            enrolled = await db.get(Machine, machine.id)
            assert enrolled is not None
            enrolled.control_capabilities = [GENERATION_REPORT_CAPABILITY]
            agent = Agent(
                name="agent-versioned-reports",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                placed_on_machine_id=machine.id,
                generation=4,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle.handle_report_actual_state(
            machine.id,
            [{"agent_id": agent_id, "actual_state": "running", "pid": 404}],
        )
        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.actual_state == "pending"
            assert agent.pid is None

        await lifecycle.handle_report_actual_state(
            machine.id,
            [
                {
                    "agent_id": agent_id,
                    "actual_state": "running",
                    "generation": 4,
                    "pid": 404,
                }
            ],
        )
        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.actual_state == "running"
            assert agent.pid == 404

    @pytest.mark.asyncio
    async def test_stale_generation_report_does_not_overwrite_current_state(
        self, lifecycle_env
    ) -> None:
        """A late report from the previous process generation is ignored."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-stale-generation",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                placed_on_machine_id=machine.id,
                generation=2,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle.handle_report_actual_state(
            machine.id,
            [
                {
                    "agent_id": agent_id,
                    "actual_state": "running",
                    "generation": 1,
                    "pid": 9876,
                }
            ],
        )

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "pending"
            assert agent.pid is None

    @pytest.mark.asyncio
    async def test_batch_preload_stop_restart_fences_stale_report(
        self, lifecycle_env, monkeypatch
    ) -> None:
        """A report preloaded before stop/restart cannot ack the new lease."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        old_lease = "pre-stop-report-owner"
        async with factory() as db:
            agent = Agent(
                name="agent-report-apply-cas",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                placed_on_machine_id=machine.id,
                generation=1,
                legacy_report_generation=1,
                lifecycle_lease_token=old_lease,
                lifecycle_lease_expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=5),
                lifecycle_delivery_state="pending_ack",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id
        await lifecycle_env["attach_to_room"](agent_id)

        batch_select_complete = asyncio.Event()
        resume_stale_report = asyncio.Event()
        original_preload = lifecycle._preload_report_agents

        async def pause_after_batch_select(db, reported_ids):
            snapshots = await original_preload(db, reported_ids)
            batch_select_complete.set()
            await resume_stale_report.wait()
            return snapshots

        monkeypatch.setattr(
            lifecycle, "_preload_report_agents", pause_after_batch_select
        )
        stale_report = asyncio.create_task(
            lifecycle.handle_report_actual_state(
                machine.id,
                [
                    {
                        "agent_id": agent_id,
                        "actual_state": "running",
                        "generation": 1,
                        "pid": 111,
                    }
                ],
            )
        )
        await asyncio.wait_for(batch_select_complete.wait(), timeout=2)

        try:
            # Commit a full stop/restart while the report still holds its
            # generation-1 batch snapshot. The restart owns generation 3.
            await asyncio.wait_for(lifecycle.request_stop(agent_id), timeout=2)
            await asyncio.wait_for(lifecycle.request_start(agent_id), timeout=2)
            async with factory() as db:
                restarted = await db.get(Agent, agent_id)
                assert restarted is not None
                assert restarted.generation == 3
                assert restarted.desired_state == "running"
                assert restarted.actual_state == "pending"
                assert restarted.lifecycle_lease_token not in (None, old_lease)
                assert restarted.lifecycle_delivery_state == "pending_ack"
                restart_lease = restarted.lifecycle_lease_token
                restart_lease_expiry = restarted.lifecycle_lease_expires_at
        except BaseException:
            resume_stale_report.set()
            await stale_report
            raise

        resume_stale_report.set()
        await stale_report

        async with factory() as db:
            agent = await db.get(Agent, agent_id)
            assert agent is not None
            assert agent.generation == 3
            assert agent.desired_state == "running"
            assert agent.actual_state == "pending"
            assert agent.pid is None
            assert agent.lifecycle_lease_token == restart_lease
            assert agent.lifecycle_lease_expires_at == restart_lease_expiry
            assert agent.lifecycle_delivery_state == "pending_ack"
            state_changes = (
                (
                    await db.execute(
                        select(ActivityLog).where(
                            ActivityLog.agent_id == agent_id,
                            ActivityLog.event_type == "state_changed",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert state_changes == []

    @pytest.mark.asyncio
    async def test_late_running_report_cannot_reverse_requested_stop(
        self, lifecycle_env
    ) -> None:
        """A periodic running report already in flight must not undo stop."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-stop-race",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
                generation=3,
                pid=4321,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle.request_stop(agent_id)
        await lifecycle.handle_report_actual_state(
            machine.id,
            [
                {
                    "agent_id": agent_id,
                    "actual_state": "running",
                    "generation": 3,
                    "pid": 4321,
                }
            ],
        )

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.desired_state == "stopped"
            assert agent.actual_state == "stopping"
            assert agent.generation == 4

        # A delayed crash from the old process generation is fenced too.
        await lifecycle.handle_report_actual_state(
            machine.id,
            [
                {
                    "agent_id": agent_id,
                    "actual_state": "crashed",
                    "generation": 3,
                    "last_crash_reason": "killed during shutdown",
                }
            ],
        )
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "stopping"

        # The stop generation's terminal observation completes the stop.
        await lifecycle.handle_report_actual_state(
            machine.id,
            [
                {
                    "agent_id": agent_id,
                    "actual_state": "crashed",
                    "generation": 4,
                    "last_crash_reason": "killed during shutdown",
                }
            ],
        )
        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "stopped"
            assert agent.unavailable_code is None

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

    # ── #227 — runtime-room-add lifecycle dispatch ────────────────

    @pytest.mark.asyncio
    async def test_on_room_added_bumps_generation_when_running(
        self, lifecycle_env
    ) -> None:
        """#227 — adding a room to a *running* agent must bump the
        generation so the machine re-sends ``sync_desired_state`` with
        the updated ``rooms`` list. Without this, the agent process
        keeps its old ``--room`` args and stays silent in the new
        room forever (the bug this issue fixes).
        """
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]
        fake_ws = lifecycle_env["fake_ws"]

        async with factory() as db:
            agent = Agent(
                name="agent-running",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
                generation=3,
                pid=1234,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)
        sent_before = len(fake_ws.sent)

        await lifecycle.on_room_added(agent_id)

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            # bump_generation increments. request_start would *also*
            # increment, but would re-place (we already have placement).
            assert agent.generation == 4
            assert agent.placed_on_machine_id == machine.id

        # A sync_desired_state frame must have been pushed to the
        # machine with the refreshed rooms list.
        assert len(fake_ws.sent) > sent_before
        frame = json.loads(fake_ws.sent[-1])
        assert frame["type"] == "sync_desired_state"
        assert frame["agent_id"] == agent_id
        assert frame["generation"] == 4

    @pytest.mark.asyncio
    async def test_on_room_added_starts_pending_agent(
        self, lifecycle_env
    ) -> None:
        """Pending/idle/stopped/crashed agents must be re-dispatched
        via ``request_start`` so adding a room to a dormant agent
        actually boots it (this was the 2026-04-12 regression fixed
        by ``test_add_room_redispatches_pending_agent``)."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]

        async with factory() as db:
            agent = Agent(
                name="agent-pending",
                engine="echo",
                desired_state="running",
                actual_state="pending",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        await lifecycle_env["attach_to_room"](agent_id)
        await lifecycle.on_room_added(agent_id)

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            # request_start places the agent on the only available
            # machine and advances it through pending.
            assert agent.placed_on_machine_id == machine.id
            assert agent.actual_state == "pending"

    @pytest.mark.asyncio
    async def test_on_room_added_noop_when_stopping(
        self, lifecycle_env
    ) -> None:
        """Agents mid-stop or already stopped-and-desired-stopped
        should not be nudged. The admin explicitly stopped them, and
        a bump would fight the stop."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]
        fake_ws = lifecycle_env["fake_ws"]

        async with factory() as db:
            agent = Agent(
                name="agent-stopping",
                engine="echo",
                desired_state="stopped",
                actual_state="stopping",
                placed_on_machine_id=machine.id,
                generation=7,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        sent_before = len(fake_ws.sent)
        await lifecycle.on_room_added(agent_id)

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            # Generation unchanged — no nudge fired.
            assert agent.generation == 7

        assert len(fake_ws.sent) == sent_before

    @pytest.mark.asyncio
    async def test_on_room_added_missing_agent_is_noop(
        self, lifecycle_env
    ) -> None:
        """Unknown agent_id must not raise; the endpoint's own 404
        path catches this, but the helper should be defensive."""
        lifecycle = lifecycle_env["lifecycle"]
        await lifecycle.on_room_added("nonexistent-id")

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


class TestAnygardenTokenCommitGate:
    """#445 — ``_acquire_anygarden_token`` must populate the in-memory
    cache *only after* the staged ``agent_tokens`` row commits.

    Pre-#445 the cache was written eagerly (right after ``db.add``), so
    a caller that rolled back its transaction — or one that never
    committed at all, like ``send_sync_batch`` — left the plaintext in
    the cache referencing a row the DB never persisted. After a restart
    the cache is gone but the agent still holds that stdin-piped token,
    so every gateway/MCP call 401s in a storm. The fix gates the cache
    on the session's ``after_commit`` event.
    """

    @staticmethod
    async def _make_agent(factory) -> str:
        async with factory() as db:
            agent = Agent(
                name="tok-agent",
                engine="echo",
                desired_state="running",
                actual_state="pending",
            )
            db.add(agent)
            await db.commit()
            return agent.id

    @pytest.mark.asyncio
    async def test_token_cached_only_after_commit(self, lifecycle_env) -> None:
        """Mint stages the row + returns the plaintext, but the cache
        stays empty until the surrounding transaction commits — then it
        holds the same plaintext and the row is durable."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        agent_id = await self._make_agent(factory)

        async with factory() as db:
            token = lifecycle._acquire_anygarden_token(db, agent_id)
            # Before commit the cache must NOT hold the token.
            assert lifecycle._token_cache.get(agent_id) is None
            await db.commit()

        # After commit the listener fires and caches the plaintext.
        assert lifecycle._token_cache.get(agent_id) == token

        # And the row actually persisted.
        async with factory() as db:
            rows = (
                await db.execute(
                    select(AgentToken).where(AgentToken.agent_id == agent_id)
                )
            ).scalars().all()
            assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_token_not_cached_on_rollback(self, lifecycle_env) -> None:
        """If the surrounding transaction rolls back, the minted token
        must NOT be left in the cache (and no row persists)."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        agent_id = await self._make_agent(factory)

        async with factory() as db:
            lifecycle._acquire_anygarden_token(db, agent_id)
            assert lifecycle._token_cache.get(agent_id) is None
            await db.rollback()

        # Rollback ⇒ no after_commit ⇒ cache stays clean.
        assert lifecycle._token_cache.get(agent_id) is None

        async with factory() as db:
            rows = (
                await db.execute(
                    select(AgentToken).where(AgentToken.agent_id == agent_id)
                )
            ).scalars().all()
            assert rows == []

    @pytest.mark.asyncio
    async def test_token_not_cached_when_never_committed(
        self, lifecycle_env
    ) -> None:
        """A read-only rebuild path (e.g. ``send_sync_batch``) that
        exits the session without committing must not poison the cache.
        The session-context exit rolls back the pending mint, and the
        ``after_commit`` listener never fires."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        agent_id = await self._make_agent(factory)

        async with factory() as db:
            lifecycle._acquire_anygarden_token(db, agent_id)
            # No commit — fall straight out of the session context.

        assert lifecycle._token_cache.get(agent_id) is None

        async with factory() as db:
            rows = (
                await db.execute(
                    select(AgentToken).where(AgentToken.agent_id == agent_id)
                )
            ).scalars().all()
            assert rows == []

    @pytest.mark.asyncio
    async def test_cache_hit_returns_committed_token(
        self, lifecycle_env
    ) -> None:
        """Once cached (post-commit), a second acquire returns the same
        plaintext without staging a second row."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        agent_id = await self._make_agent(factory)

        async with factory() as db:
            first = lifecycle._acquire_anygarden_token(db, agent_id)
            await db.commit()
        assert lifecycle._token_cache.get(agent_id) == first

        # Second acquire is a pure cache hit — no new row, same token.
        async with factory() as db:
            second = lifecycle._acquire_anygarden_token(db, agent_id)
            await db.commit()
        assert second == first

        async with factory() as db:
            rows = (
                await db.execute(
                    select(AgentToken).where(AgentToken.agent_id == agent_id)
                )
            ).scalars().all()
            assert len(rows) == 1


class TestSharedFilesBackfillOnRunningTransition:
    """#255 — The spawner prunes ``<agent_root>/memory/`` on every
    respawn, so any room shared files that were already materialised
    there are wiped. The cluster only schedules a backfill on the
    *first* room join (``ensure_agent_in_room`` ``created=True``),
    which means respawn leaves the agent permanently without its
    shared files until someone re-uploads.

    The fix: when ``handle_report_actual_state`` observes a transition
    *into* ``running``, re-push every room shared file to that agent's
    machine. Idempotent thanks to the daemon's ``content_sha256``
    compare — redundant re-sends after a no-op transition just skip.
    """

    @staticmethod
    async def _seed_shared_file(
        factory,
        *,
        room_id: str,
        room_files_dir,
        storage_name: str = "note.md",
        body: bytes = b"# hello\n",
    ) -> str:
        """Write bytes to ``room_files_dir`` and insert a matching
        ``RoomSharedFile`` row. Returns the storage-relative path
        used by the DB row.
        """
        from anygarden.db.models import RoomSharedFile

        room_files_dir.mkdir(parents=True, exist_ok=True)
        rel = f"{room_id}/{storage_name}"
        path = room_files_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

        import hashlib
        sha = hashlib.sha256(body).hexdigest()
        async with factory() as db:
            row = RoomSharedFile(
                room_id=room_id,
                filename=storage_name,
                storage_name=storage_name,
                storage_path=rel,
                sha256=sha,
                size_bytes=len(body),
                mime="text/plain",
            )
            db.add(row)
            await db.commit()
        return rel

    @pytest.mark.asyncio
    async def test_running_transition_pushes_existing_shared_files(
        self, lifecycle_env, tmp_path
    ) -> None:
        """pending → running must trigger agent_memory_shared_file_write
        for every shared file currently in the room."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]
        room_id = lifecycle_env["room_id"]
        fake_ws = lifecycle_env["fake_ws"]

        room_files_dir = tmp_path / "room_files"
        lifecycle._room_files_dir = room_files_dir  # test-only injection

        async with factory() as db:
            agent = Agent(
                name="respawn-agent",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                placed_on_machine_id=machine.id,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id
        await lifecycle_env["attach_to_room"](agent_id)
        await self._seed_shared_file(
            factory,
            room_id=room_id,
            room_files_dir=room_files_dir,
            storage_name="note.md",
            body=b"shared content\n",
        )

        fake_ws.sent.clear()
        await lifecycle.handle_report_actual_state(
            machine.id,
            [{"agent_id": agent_id, "actual_state": "running", "pid": 1234}],
        )

        frames = [json.loads(s) for s in fake_ws.sent]
        writes = [f for f in frames if f.get("type") == "agent_memory_shared_file_write"]
        assert len(writes) == 1, (
            f"expected one backfill frame, got frames={frames!r}"
        )
        assert writes[0]["agent_id"] == agent_id
        assert writes[0]["storage_name"] == "note.md"
        assert writes[0]["content"] == "shared content\n"

    @pytest.mark.asyncio
    async def test_running_to_running_does_not_rebackfill(
        self, lifecycle_env, tmp_path
    ) -> None:
        """Heartbeat reports that keep actual_state=running should NOT
        retrigger backfill — otherwise every heartbeat floods the
        machine bus with duplicates."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]
        room_id = lifecycle_env["room_id"]
        fake_ws = lifecycle_env["fake_ws"]

        room_files_dir = tmp_path / "room_files"
        lifecycle._room_files_dir = room_files_dir

        async with factory() as db:
            agent = Agent(
                name="running-agent",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=machine.id,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id
        await lifecycle_env["attach_to_room"](agent_id)
        await self._seed_shared_file(
            factory, room_id=room_id, room_files_dir=room_files_dir,
        )

        fake_ws.sent.clear()
        await lifecycle.handle_report_actual_state(
            machine.id,
            [{"agent_id": agent_id, "actual_state": "running"}],
        )

        frames = [json.loads(s) for s in fake_ws.sent]
        writes = [f for f in frames if f.get("type") == "agent_memory_shared_file_write"]
        assert writes == [], (
            f"heartbeat with unchanged state must not backfill: {frames!r}"
        )

    @pytest.mark.asyncio
    async def test_no_room_files_dir_skips_gracefully(
        self, lifecycle_env
    ) -> None:
        """When the lifecycle was built without a ``room_files_dir``
        (pre-#255 tests, or deployments that never enabled shared
        files), the running transition must still work — we just
        can't backfill, so the frame list stays empty and no errors
        raise."""
        factory = lifecycle_env["factory"]
        lifecycle = lifecycle_env["lifecycle"]
        machine = lifecycle_env["machine"]
        room_id = lifecycle_env["room_id"]
        fake_ws = lifecycle_env["fake_ws"]

        # No _room_files_dir on lifecycle.
        assert not hasattr(lifecycle, "_room_files_dir") or \
               getattr(lifecycle, "_room_files_dir") is None

        async with factory() as db:
            agent = Agent(
                name="no-dir-agent",
                engine="echo",
                desired_state="running",
                actual_state="pending",
                placed_on_machine_id=machine.id,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id
        await lifecycle_env["attach_to_room"](agent_id)

        fake_ws.sent.clear()
        # Must not raise.
        await lifecycle.handle_report_actual_state(
            machine.id,
            [{"agent_id": agent_id, "actual_state": "running"}],
        )

        frames = [json.loads(s) for s in fake_ws.sent]
        writes = [f for f in frames if f.get("type") == "agent_memory_shared_file_write"]
        assert writes == []


class TestSweepStaleAgents:
    """#447 Wave 1a — heartbeat reaper for agents stuck ``running`` on a
    dead/offline machine.

    Dual gate: ``last_heartbeat_at`` older than threshold AND the placed
    machine is not ``online``. The starting-agent guard is the load-
    bearing safety property — a spawning agent has a NULL/old heartbeat
    and must never be reaped.
    """

    async def _make_offline_machine(self, factory, user) -> str:
        async with factory() as db:
            machine = Machine(
                name="dead-machine",
                hostname="host-dead",
                owner_user_id=user.id,
                status="offline",
                max_agents=5,
            )
            db.add(machine)
            await db.commit()
            return machine.id

    @pytest.mark.asyncio
    async def test_running_stale_on_offline_machine_is_crashed(
        self, lifecycle_env
    ) -> None:
        factory = lifecycle_env["factory"]
        user = lifecycle_env["user"]
        offline_id = await self._make_offline_machine(factory, user)

        stale = datetime.now(timezone.utc) - timedelta(seconds=300)
        async with factory() as db:
            agent = Agent(
                name="stale-running",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=offline_id,
                last_heartbeat_at=stale,
                pid=4242,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        n = await sweep_stale_agents(factory, threshold_sec=120)
        assert n == 1

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "crashed"
            assert agent.last_crash_reason == "heartbeat_stale"

            logs = (
                await db.execute(
                    select(ActivityLog).where(
                        ActivityLog.agent_id == agent_id,
                        ActivityLog.event_type == "state_changed",
                    )
                )
            ).scalars().all()
            assert len(logs) == 1
            details = logs[0].details
            assert details["from"] == "running"
            assert details["to"] == "crashed"
            assert details["reason"] == "heartbeat_stale"

    @pytest.mark.asyncio
    async def test_starting_agent_with_null_heartbeat_is_untouched(
        self, lifecycle_env
    ) -> None:
        """A ``starting`` agent mid-spawn has a NULL heartbeat (it is only
        stamped on the running transition) and must never be reaped."""
        factory = lifecycle_env["factory"]
        user = lifecycle_env["user"]
        offline_id = await self._make_offline_machine(factory, user)

        async with factory() as db:
            null_hb = Agent(
                name="starting-null-hb",
                engine="echo",
                desired_state="running",
                actual_state="starting",
                placed_on_machine_id=offline_id,
                last_heartbeat_at=None,
            )
            db.add(null_hb)
            # Also a starting agent that happens to carry an old heartbeat
            # (e.g. a prior crash/restart cycle): still must not be reaped,
            # because the filter only matches ``running``.
            old_hb = Agent(
                name="starting-old-hb",
                engine="echo",
                desired_state="running",
                actual_state="starting",
                placed_on_machine_id=offline_id,
                last_heartbeat_at=datetime.now(timezone.utc)
                - timedelta(seconds=600),
            )
            db.add(old_hb)
            await db.commit()
            null_id = null_hb.id
            old_id = old_hb.id

        n = await sweep_stale_agents(factory, threshold_sec=120)
        assert n == 0

        async with factory() as db:
            for aid in (null_id, old_id):
                agent = (
                    await db.execute(select(Agent).where(Agent.id == aid))
                ).scalar_one()
                assert agent.actual_state == "starting"
                assert agent.last_crash_reason is None

    @pytest.mark.asyncio
    async def test_running_recent_heartbeat_is_untouched(
        self, lifecycle_env
    ) -> None:
        factory = lifecycle_env["factory"]
        user = lifecycle_env["user"]
        offline_id = await self._make_offline_machine(factory, user)

        recent = datetime.now(timezone.utc) - timedelta(seconds=10)
        async with factory() as db:
            agent = Agent(
                name="fresh-running",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=offline_id,
                last_heartbeat_at=recent,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        n = await sweep_stale_agents(factory, threshold_sec=120)
        assert n == 0

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "running"

    @pytest.mark.asyncio
    async def test_running_stale_on_online_machine_is_untouched(
        self, lifecycle_env
    ) -> None:
        """Dual gate: a stale heartbeat alone (machine still ``online``)
        could be a slow report from a live machine — do not reap."""
        factory = lifecycle_env["factory"]
        # The fixture machine is ``online``.
        online_machine = lifecycle_env["machine"]

        stale = datetime.now(timezone.utc) - timedelta(seconds=300)
        async with factory() as db:
            agent = Agent(
                name="stale-but-online",
                engine="echo",
                desired_state="running",
                actual_state="running",
                placed_on_machine_id=online_machine.id,
                last_heartbeat_at=stale,
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        n = await sweep_stale_agents(factory, threshold_sec=120)
        assert n == 0

        async with factory() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == agent_id))
            ).scalar_one()
            assert agent.actual_state == "running"
