"""Integration tests for the declarative desired-state reconcile cycle."""

from __future__ import annotations

import json
import secrets
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
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
from doorae.auth.machine_token import generate_machine_token, hash_machine_token
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus


class FakeWS:
    """Captures frames sent to the machine WebSocket."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    def reset(self) -> None:
        self.sent.clear()

    def last_frame(self) -> dict:
        return json.loads(self.sent[-1])


@pytest_asyncio.fixture()
async def reconcile_env():
    """Set up in-memory SQLite DB with all required entities for reconcile tests."""
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

    fake_ws = FakeWS()

    async with factory() as db:
        # User (admin)
        user = User(email="admin@test.com", password_hash="x")
        db.add(user)
        await db.flush()

        # Machine (online)
        machine = Machine(
            name="reconcile-machine",
            hostname="host-reconcile",
            owner_user_id=user.id,
            status="online",
            max_agents=5,
        )
        db.add(machine)
        await db.flush()

        # MachineToken (valid)
        plaintext = generate_machine_token()
        hashed, hint = hash_machine_token(plaintext)
        machine_token = MachineToken(
            machine_id=machine.id,
            token_hash=hashed,
            lookup_hint=hint,
        )
        db.add(machine_token)

        # MachineEngine (claude-code)
        db.add(MachineEngine(machine_id=machine.id, engine="claude-code"))

        # Project
        project = Project(name="reconcile-project")
        db.add(project)
        await db.flush()

        # Room
        room = Room(project_id=project.id, name="reconcile-room")
        db.add(room)
        await db.flush()

        # Agent (idle, engine=claude-code)
        agent = Agent(
            name="reconcile-agent",
            engine="claude-code",
            desired_state="idle",
            actual_state="idle",
        )
        db.add(agent)
        await db.flush()

        # Participant (agent in room)
        participant = Participant(room_id=room.id, agent_id=agent.id)
        db.add(participant)

        await db.commit()

        # Register machine WS in bus
        await bus.register(machine.id, fake_ws)

        machine_id = machine.id
        agent_id = agent.id

    yield {
        "config": config,
        "factory": factory,
        "bus": bus,
        "lifecycle": lifecycle,
        "machine_id": machine_id,
        "agent_id": agent_id,
        "fake_ws": fake_ws,
        "plaintext_token": plaintext,
    }

    await engine.dispose()


class TestDeclarativeReconcile:
    @pytest.mark.asyncio
    async def test_request_start_sends_sync_desired_state(self, reconcile_env) -> None:
        """request_start sends sync_desired_state with desired_state='running',
        generation==1, and no agent_token in the frame."""
        lifecycle = reconcile_env["lifecycle"]
        agent_id = reconcile_env["agent_id"]
        fake_ws = reconcile_env["fake_ws"]

        await lifecycle.request_start(agent_id)

        assert len(fake_ws.sent) >= 1
        frame = fake_ws.last_frame()

        assert frame["type"] == "sync_desired_state"
        assert frame["desired_state"] == "running"
        assert frame["generation"] == 1
        # agent_token must NOT be in the frame (tokens are issued on demand)
        assert "agent_token" not in frame

    @pytest.mark.asyncio
    async def test_request_stop_sends_sync_stopped(self, reconcile_env) -> None:
        """request_stop sends sync_desired_state with desired_state='stopped'."""
        lifecycle = reconcile_env["lifecycle"]
        agent_id = reconcile_env["agent_id"]
        fake_ws = reconcile_env["fake_ws"]
        factory = reconcile_env["factory"]
        machine_id = reconcile_env["machine_id"]

        # Start agent first to place it on the machine
        await lifecycle.request_start(agent_id)
        fake_ws.reset()

        # Now stop it
        await lifecycle.request_stop(agent_id)

        assert len(fake_ws.sent) >= 1
        frame = fake_ws.last_frame()
        assert frame["type"] == "sync_desired_state"
        assert frame["desired_state"] == "stopped"

    @pytest.mark.asyncio
    async def test_handle_token_request_issues_token(self, reconcile_env) -> None:
        """handle_token_request returns token grants with agent tokens starting with 'agt_'."""
        lifecycle = reconcile_env["lifecycle"]
        agent_id = reconcile_env["agent_id"]
        machine_id = reconcile_env["machine_id"]

        # Place agent on machine via request_start
        await lifecycle.request_start(agent_id)

        # Request a token for the agent
        grants = await lifecycle.handle_token_request(machine_id, [agent_id])

        assert len(grants) == 1
        grant = grants[0]
        assert grant["agent_id"] == agent_id
        assert grant["type"] == "token_grant"
        assert grant["agent_token"].startswith("agt_")

    @pytest.mark.asyncio
    async def test_handle_report_actual_state_updates_db(self, reconcile_env) -> None:
        """handle_report_actual_state updates agent's actual_state and pid in DB."""
        lifecycle = reconcile_env["lifecycle"]
        agent_id = reconcile_env["agent_id"]
        machine_id = reconcile_env["machine_id"]
        factory = reconcile_env["factory"]

        # Place agent on machine first so the report is accepted
        await lifecycle.request_start(agent_id)

        # Machine reports actual state
        await lifecycle.handle_report_actual_state(
            machine_id,
            [{"agent_id": agent_id, "actual_state": "running", "pid": 12345}],
        )

        async with factory() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one()
            assert agent.actual_state == "running"
            assert agent.pid == 12345

    @pytest.mark.asyncio
    async def test_bump_generation_pushes_sync(self, reconcile_env) -> None:
        """bump_generation sends sync_desired_state with generation==2 after start."""
        lifecycle = reconcile_env["lifecycle"]
        agent_id = reconcile_env["agent_id"]
        fake_ws = reconcile_env["fake_ws"]

        # Start agent first (generation becomes 1)
        await lifecycle.request_start(agent_id)
        fake_ws.reset()

        # Bump generation (should become 2)
        await lifecycle.bump_generation(agent_id)

        assert len(fake_ws.sent) >= 1
        frame = fake_ws.last_frame()
        assert frame["type"] == "sync_desired_state"
        assert frame["generation"] == 2

    @pytest.mark.asyncio
    async def test_send_sync_batch(self, reconcile_env) -> None:
        """send_sync_batch sends a sync_batch frame containing the placed agent."""
        lifecycle = reconcile_env["lifecycle"]
        agent_id = reconcile_env["agent_id"]
        machine_id = reconcile_env["machine_id"]
        fake_ws = reconcile_env["fake_ws"]

        # Start agent to place it on the machine
        await lifecycle.request_start(agent_id)
        fake_ws.reset()

        # Send sync batch
        await lifecycle.send_sync_batch(machine_id)

        assert len(fake_ws.sent) >= 1
        frame = fake_ws.last_frame()
        assert frame["type"] == "sync_batch"
        assert isinstance(frame["agents"], list)

        agent_ids_in_batch = [a["agent_id"] for a in frame["agents"]]
        assert agent_id in agent_ids_in_batch
