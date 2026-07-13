"""Tests for the machine daemon WebSocket handler."""

from __future__ import annotations

import json
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    Machine,
    MachineEngine,
    MachineToken,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.auth.machine_token import generate_machine_token, hash_machine_token
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.ws.machine_handler import _authenticate_machine, _handle_register


@pytest_asyncio.fixture()
async def handler_env():
    """Set up DB with a machine, valid token, agent, room, and participant."""
    config = AnygardenSettings(
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

    async with factory() as db:
        user = User(email="mach@test.com", password_hash="x")
        db.add(user)
        await db.flush()

        machine = Machine(
            name="test-machine",
            hostname="test-host",
            owner_user_id=user.id,
            status="offline",
            max_agents=5,
        )
        db.add(machine)
        await db.flush()

        plaintext = generate_machine_token()
        hashed, hint = hash_machine_token(plaintext)
        token_record = MachineToken(
            machine_id=machine.id,
            token_hash=hashed,
            lookup_hint=hint,
        )
        db.add(token_record)

        # Create a project, room, agent, and participant for new tests
        project = Project(name="test-project")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="test-room")
        db.add(room)
        await db.flush()

        agent = Agent(
            name="test-agent",
            engine="echo",
            desired_state="running",
            actual_state="pending",
            placed_on_machine_id=machine.id,
        )
        db.add(agent)
        await db.flush()

        participant = Participant(room_id=room.id, agent_id=agent.id)
        db.add(participant)

        await db.commit()

        await db.refresh(machine)
        await db.refresh(agent)
        await db.refresh(room)

        yield {
            "config": config,
            "factory": factory,
            "bus": bus,
            "lifecycle": lifecycle,
            "machine": machine,
            "token": plaintext,
            "user": user,
            "agent": agent,
            "room": room,
        }

    await engine.dispose()


class TestMachineHandler:
    @pytest.mark.asyncio
    async def test_authenticate_valid_token(self, handler_env) -> None:
        """A valid machine token should authenticate successfully."""
        factory = handler_env["factory"]
        machine = handler_env["machine"]
        token = handler_env["token"]

        async with factory() as db:
            result = await _authenticate_machine(
                db,
                machine.id,
                f"anygarden.v1, bearer.{token}",
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_authenticate_bad_token(self, handler_env) -> None:
        """An invalid token should fail authentication."""
        factory = handler_env["factory"]
        machine = handler_env["machine"]

        async with factory() as db:
            result = await _authenticate_machine(
                db,
                machine.id,
                "anygarden.v1, bearer.mch_bad_token_here",
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_register_sets_online_and_saves_engines(self, handler_env) -> None:
        """A register frame should set machine online and persist engines."""
        factory = handler_env["factory"]
        machine = handler_env["machine"]

        await _handle_register(
            factory,
            machine.id,
            {
                "type": "register",
                "engines": ["echo", "llm"],
                "daemon_version": "0.4.0",
            },
        )

        async with factory() as db:
            result = await db.execute(
                select(Machine).where(Machine.id == machine.id)
            )
            m = result.scalar_one()
            assert m.status == "online"
            assert m.daemon_version == "0.4.0"
            assert m.daemon_last_seen_at is not None

            result = await db.execute(
                select(MachineEngine).where(MachineEngine.machine_id == machine.id)
            )
            engines = result.scalars().all()
            engine_names = {e.engine for e in engines}
            assert engine_names == {"echo", "llm"}

    @pytest.mark.asyncio
    async def test_register_persists_system_info(self, handler_env) -> None:
        """A register frame with system_info persists it, overwriting the
        placeholder hostname (issue #523)."""
        factory = handler_env["factory"]
        machine = handler_env["machine"]

        await _handle_register(
            factory,
            machine.id,
            {
                "type": "register",
                "engines": [],
                "system_info": {
                    "hostname": "real-worker-01",
                    "lan_ip": "192.168.1.42",
                    "os_platform": "Linux-6.17-x86_64",
                    "cpu_cores": 8,
                    "memory_gb": 64.0,
                },
            },
        )

        async with factory() as db:
            m = (
                await db.execute(select(Machine).where(Machine.id == machine.id))
            ).scalar_one()
            assert m.hostname == "real-worker-01"  # overwrote "test-host"
            assert m.lan_ip == "192.168.1.42"
            assert m.os_platform == "Linux-6.17-x86_64"
            assert m.cpu_cores == 8
            assert m.memory_gb == 64.0

    @pytest.mark.asyncio
    async def test_register_partial_system_info_does_not_clobber(
        self, handler_env
    ) -> None:
        """Empty / zero probe results must not wipe a previously-good value."""
        factory = handler_env["factory"]
        machine = handler_env["machine"]

        # First register with good values.
        await _handle_register(
            factory,
            machine.id,
            {
                "type": "register",
                "system_info": {
                    "hostname": "real-worker-01",
                    "cpu_cores": 8,
                    "memory_gb": 64.0,
                },
            },
        )
        # Second register where collection failed on several fields.
        await _handle_register(
            factory,
            machine.id,
            {
                "type": "register",
                "system_info": {
                    "hostname": "",
                    "cpu_cores": 0,
                    "memory_gb": 0.0,
                    "lan_ip": None,
                },
            },
        )

        async with factory() as db:
            m = (
                await db.execute(select(Machine).where(Machine.id == machine.id))
            ).scalar_one()
            assert m.hostname == "real-worker-01"  # not clobbered by ""
            assert m.cpu_cores == 8  # not clobbered by 0
            assert m.memory_gb == 64.0

    @pytest.mark.asyncio
    async def test_report_actual_state_updates_db(self, handler_env) -> None:
        """report_actual_state should update the agent's actual_state and pid in DB."""
        factory = handler_env["factory"]
        machine = handler_env["machine"]
        agent = handler_env["agent"]
        lifecycle = handler_env["lifecycle"]

        await lifecycle.handle_report_actual_state(machine.id, [
            {
                "agent_id": agent.id,
                "actual_state": "running",
                "pid": 12345,
            },
        ])

        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent.id))
            updated = result.scalar_one()
            assert updated.actual_state == "running"
            assert updated.pid == 12345

    @pytest.mark.asyncio
    async def test_token_request_returns_grants(self, handler_env) -> None:
        """handle_token_request should return token_grant dicts for valid agents."""
        factory = handler_env["factory"]
        machine = handler_env["machine"]
        agent = handler_env["agent"]
        lifecycle = handler_env["lifecycle"]

        grants = await lifecycle.handle_token_request(machine.id, [agent.id])

        assert len(grants) == 1
        assert grants[0]["agent_id"] == agent.id
        assert grants[0]["type"] == "token_grant"
        assert grants[0]["agent_token"].startswith("agt_")

    @pytest.mark.asyncio
    async def test_send_sync_batch_after_report(self, handler_env) -> None:
        """sync_batch should be sent after report_actual_state."""
        factory = handler_env["factory"]
        machine = handler_env["machine"]
        agent = handler_env["agent"]
        bus = handler_env["bus"]
        lifecycle = handler_env["lifecycle"]

        sent_data: list[str] = []

        class FakeWS:
            async def send_text(self, data: str) -> None:
                sent_data.append(data)

        await bus.register(machine.id, FakeWS())

        # Report actual state, then send sync_batch (as the handler does)
        await lifecycle.handle_report_actual_state(machine.id, [
            {"agent_id": agent.id, "actual_state": "running", "pid": 100},
        ])
        await lifecycle.send_sync_batch(machine.id)

        # Should have received the sync_batch frame
        assert len(sent_data) >= 1
        batch_frame = json.loads(sent_data[-1])
        assert batch_frame["type"] == "sync_batch"
        assert isinstance(batch_frame["agents"], list)
        # The agent placed on this machine should appear in the batch
        agent_ids = [a["agent_id"] for a in batch_frame["agents"]]
        assert agent.id in agent_ids

        await bus.unregister(machine.id)

    @pytest.mark.asyncio
    async def test_request_replacement_triggers_restart(self, handler_env) -> None:
        """handle_request_replacement should reset agent and attempt re-placement."""
        factory = handler_env["factory"]
        machine = handler_env["machine"]
        agent = handler_env["agent"]
        lifecycle = handler_env["lifecycle"]

        # First set agent to running state so replacement makes sense
        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent.id))
            a = result.scalar_one()
            a.actual_state = "running"
            a.pid = 5555
            await db.commit()

        # Mock request_start to avoid placement logic needing online machines
        original_request_start = lifecycle.request_start
        started_ids: list[str] = []

        async def mock_request_start(aid: str) -> None:
            started_ids.append(aid)

        lifecycle.request_start = mock_request_start
        try:
            await lifecycle.handle_request_replacement(
                machine.id, agent.id, "engine OOM"
            )
        finally:
            lifecycle.request_start = original_request_start

        # Agent should have been reset
        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent.id))
            replaced = result.scalar_one()
            assert replaced.actual_state == "pending"
            assert replaced.pid is None
            assert replaced.placed_on_machine_id is None
            assert replaced.last_crash_reason == "engine OOM"

        # request_start should have been called for re-placement
        assert started_ids == [agent.id]

    @pytest.mark.asyncio
    async def test_bus_send_delivers_spawn_frame(self, handler_env) -> None:
        """MachineBus.send should deliver a frame to the registered WebSocket."""
        bus = handler_env["bus"]
        machine = handler_env["machine"]

        sent_data: list[str] = []

        class FakeWS:
            async def send_text(self, data: str) -> None:
                sent_data.append(data)

        await bus.register(machine.id, FakeWS())

        result = await bus.send(machine.id, {
            "type": "spawn_agent",
            "agent_id": "test-agent-123",
        })
        assert result is True
        assert len(sent_data) == 1
        parsed = json.loads(sent_data[0])
        assert parsed["type"] == "spawn_agent"
        assert parsed["agent_id"] == "test-agent-123"

        await bus.unregister(machine.id)
        assert not bus.is_connected(machine.id)
