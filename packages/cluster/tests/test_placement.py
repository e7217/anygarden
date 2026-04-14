"""Tests for bin-pack machine selection (placement)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, Base, Machine, MachineEngine, User
from doorae.scheduler.machine_bus import MachineBus
from doorae.scheduler.placement import NoSuitableMachineError, select_machine_for


@pytest_asyncio.fixture()
async def placement_env():
    """Set up an in-memory DB with machines, engines, and a bus."""
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()

    async with factory() as db:
        user = User(email="owner@test.com", password_hash="x")
        db.add(user)
        await db.flush()

        # Machine A: supports "echo", max_agents=2
        machine_a = Machine(
            name="machine-a",
            hostname="host-a",
            owner_user_id=user.id,
            status="online",
            max_agents=2,
            labels={"gpu": "true"},
        )
        db.add(machine_a)
        await db.flush()
        db.add(MachineEngine(machine_id=machine_a.id, engine="echo"))
        db.add(MachineEngine(machine_id=machine_a.id, engine="llm"))

        # Machine B: supports "echo", max_agents=3
        machine_b = Machine(
            name="machine-b",
            hostname="host-b",
            owner_user_id=user.id,
            status="online",
            max_agents=3,
        )
        db.add(machine_b)
        await db.flush()
        db.add(MachineEngine(machine_id=machine_b.id, engine="echo"))

        # Machine C: offline, supports "echo"
        machine_c = Machine(
            name="machine-c",
            hostname="host-c",
            owner_user_id=user.id,
            status="offline",
            max_agents=5,
        )
        db.add(machine_c)
        await db.flush()
        db.add(MachineEngine(machine_id=machine_c.id, engine="echo"))

        await db.commit()

        # Connect machines A and B to the bus (not C)
        class FakeWS:
            async def send_text(self, data: str) -> None:
                pass

        await bus.register(machine_a.id, FakeWS())
        await bus.register(machine_b.id, FakeWS())

        yield {
            "factory": factory,
            "bus": bus,
            "machine_a": machine_a,
            "machine_b": machine_b,
            "machine_c": machine_c,
            "user": user,
        }

    await engine.dispose()


class TestPlacement:
    @pytest.mark.asyncio
    async def test_bin_pack_selects_machine_with_fewest_agents(
        self, placement_env
    ) -> None:
        """With no running agents, either machine is valid; add agents to A, B should be picked."""
        bus = placement_env["bus"]
        factory = placement_env["factory"]
        machine_a = placement_env["machine_a"]
        machine_b = placement_env["machine_b"]

        # Add a running agent on machine_a
        async with factory() as db:
            agent = Agent(
                name="a1",
                engine="echo",
                placed_on_machine_id=machine_a.id,
                actual_state="running",
            )
            db.add(agent)
            await db.commit()

        async with factory() as db:
            selected = await select_machine_for("echo", db, bus)
            # B has 0 agents, A has 1 → B should be selected
            assert selected.id == machine_b.id

    @pytest.mark.asyncio
    async def test_engine_filter(self, placement_env) -> None:
        """Only machine A supports 'llm' engine."""
        bus = placement_env["bus"]
        factory = placement_env["factory"]
        machine_a = placement_env["machine_a"]

        async with factory() as db:
            selected = await select_machine_for("llm", db, bus)
            assert selected.id == machine_a.id

    @pytest.mark.asyncio
    async def test_label_matching(self, placement_env) -> None:
        """Only machine A has gpu=true label."""
        bus = placement_env["bus"]
        factory = placement_env["factory"]
        machine_a = placement_env["machine_a"]

        async with factory() as db:
            selected = await select_machine_for(
                "echo", db, bus, required_labels={"gpu": "true"}
            )
            assert selected.id == machine_a.id

    @pytest.mark.asyncio
    async def test_capacity_full_raises(self, placement_env) -> None:
        """When all machines are at max capacity, raise NoSuitableMachineError."""
        bus = placement_env["bus"]
        factory = placement_env["factory"]
        machine_a = placement_env["machine_a"]
        machine_b = placement_env["machine_b"]

        async with factory() as db:
            # Fill machine A (max_agents=2)
            for i in range(2):
                db.add(Agent(
                    name=f"a-fill-{i}",
                    engine="echo",
                    placed_on_machine_id=machine_a.id,
                    actual_state="running",
                ))
            # Fill machine B (max_agents=3)
            for i in range(3):
                db.add(Agent(
                    name=f"b-fill-{i}",
                    engine="echo",
                    placed_on_machine_id=machine_b.id,
                    actual_state="running",
                ))
            await db.commit()

        async with factory() as db:
            with pytest.raises(NoSuitableMachineError):
                await select_machine_for("echo", db, bus)

    @pytest.mark.asyncio
    async def test_no_ws_connection_skipped(self, placement_env) -> None:
        """Machine C is offline and has no WS connection — should be skipped."""
        bus = placement_env["bus"]
        factory = placement_env["factory"]

        # Unregister A and B so no machines are connected
        await bus.unregister(placement_env["machine_a"].id)
        await bus.unregister(placement_env["machine_b"].id)

        async with factory() as db:
            with pytest.raises(NoSuitableMachineError):
                await select_machine_for("echo", db, bus)
