"""Issue #379 — openhands agents are auto-reset to orphan state on cluster
startup so the existing machine-reconnect → ``_place_orphaned_agents`` path
respawns them with a fresh process, restoring the in-process SDK state."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from sqlalchemy import select

from anygarden.app import _reset_openhands_agents_for_restart
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, Base, Machine, User


@pytest_asyncio.fixture()
async def factory():
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = build_session_factory(engine)
    # A real Machine row so ``placed_on_machine_id='m-existing'`` is FK-valid.
    async with factory() as db:
        owner = User(email="owner@test.com", password_hash="x")
        db.add(owner)
        await db.flush()
        db.add(
            Machine(
                id="m-existing",
                name="existing",
                hostname="h",
                owner_user_id=owner.id,
                status="online",
                max_agents=5,
            )
        )
        await db.commit()
    yield factory
    await engine.dispose()


async def _make_agent(
    factory,
    *,
    engine: str,
    actual_state: str,
    desired_state: str = "running",
    placed_on_machine_id: str | None = "m-existing",
    pid: int | None = 1234,
) -> str:
    async with factory() as db:
        agent = Agent(
            name=f"{engine}-{actual_state}",
            engine=engine,
            desired_state=desired_state,
            actual_state=actual_state,
            placed_on_machine_id=placed_on_machine_id,
            pid=pid,
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        return agent.id


async def _reload(factory, agent_id: str) -> Agent:
    async with factory() as db:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one()


class TestResetOpenhandsAgentsForRestart:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["running", "starting", "stopping"])
    async def test_openhands_active_states_are_reset_to_orphan(
        self, factory, state: str
    ) -> None:
        agent_id = await _make_agent(factory, engine="openhands", actual_state=state)

        async with factory() as db:
            reset_ids = await _reset_openhands_agents_for_restart(db)
            await db.commit()

        assert reset_ids == [agent_id]
        agent = await _reload(factory, agent_id)
        assert agent.actual_state == "pending"
        assert agent.desired_state == "running"
        assert agent.placed_on_machine_id is None
        assert agent.pid is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["stopped", "pending", "idle"])
    async def test_openhands_inactive_states_are_untouched(
        self, factory, state: str
    ) -> None:
        agent_id = await _make_agent(
            factory,
            engine="openhands",
            actual_state=state,
            desired_state="stopped" if state == "stopped" else "running",
        )

        async with factory() as db:
            reset_ids = await _reset_openhands_agents_for_restart(db)
            await db.commit()

        assert reset_ids == []
        agent = await _reload(factory, agent_id)
        assert agent.actual_state == state
        assert agent.placed_on_machine_id == "m-existing"
        assert agent.pid == 1234

    @pytest.mark.asyncio
    @pytest.mark.parametrize("engine", ["claude-code", "codex", "gemini-cli"])
    async def test_non_openhands_engines_are_isolated(
        self, factory, engine: str
    ) -> None:
        agent_id = await _make_agent(factory, engine=engine, actual_state="running")

        async with factory() as db:
            reset_ids = await _reset_openhands_agents_for_restart(db)
            await db.commit()

        assert reset_ids == []
        agent = await _reload(factory, agent_id)
        assert agent.actual_state == "running"
        assert agent.placed_on_machine_id == "m-existing"
        assert agent.pid == 1234

    @pytest.mark.asyncio
    async def test_no_targets_returns_empty(self, factory) -> None:
        async with factory() as db:
            reset_ids = await _reset_openhands_agents_for_restart(db)
            await db.commit()

        assert reset_ids == []

    @pytest.mark.asyncio
    async def test_mixed_population_only_resets_openhands(self, factory) -> None:
        oh_running = await _make_agent(
            factory, engine="openhands", actual_state="running"
        )
        oh_stopped = await _make_agent(
            factory,
            engine="openhands",
            actual_state="stopped",
            desired_state="stopped",
        )
        cc_running = await _make_agent(
            factory, engine="claude-code", actual_state="running"
        )

        async with factory() as db:
            reset_ids = await _reset_openhands_agents_for_restart(db)
            await db.commit()

        assert set(reset_ids) == {oh_running}

        oh_running_agent = await _reload(factory, oh_running)
        assert oh_running_agent.actual_state == "pending"
        assert oh_running_agent.placed_on_machine_id is None

        oh_stopped_agent = await _reload(factory, oh_stopped)
        assert oh_stopped_agent.actual_state == "stopped"

        cc_agent = await _reload(factory, cc_running)
        assert cc_agent.actual_state == "running"
        assert cc_agent.placed_on_machine_id == "m-existing"
