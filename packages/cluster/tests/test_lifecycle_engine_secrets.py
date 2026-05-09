"""Integration tests for ``_build_sync_frame`` engine_secrets (#359).

Locks the matrix that the user's reported regression (#359) cared
about: flipping ``DOORAE_LLM_GATEWAY_ENABLED=true`` populates the
spawn-frame env keys for openhands agents *and* leaves the three
CLI engines untouched. The unit tests in
``test_gateway_secrets_population.py`` cover the helper in isolation;
this file exercises the end-to-end frame builder so a future change
to ``_build_sync_frame`` that drops the helper call (or wires it
wrong) shows up here loudly.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, AgentToken, Base
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus


@pytest_asyncio.fixture()
async def env():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield {"factory": factory}
    await engine.dispose()


async def _build_frame(
    factory,
    *,
    engine_name: str,
    gateway_enabled: bool,
    cluster_external_url: str | None,
) -> dict:
    bus = MachineBus()
    lifecycle = AgentLifecycle(
        db_factory=factory,
        machine_bus=bus,
        cluster_external_url=cluster_external_url,
        llm_gateway_enabled=gateway_enabled,
    )
    async with factory() as db:
        agent = Agent(
            engine=engine_name,
            name=f"a-{engine_name}",
            desired_state="idle",
            actual_state="idle",
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)

        frame = await lifecycle._build_sync_frame(db, agent, rooms=[])
        # _build_sync_frame stages the AgentToken row via ``db.add``
        # but doesn't commit — production paths
        # (``request_start`` / ``handle_report``) commit at the
        # outer scope. Mirror that here so a follow-up session can
        # observe the row.
        await db.commit()
        return frame


class TestOpenHandsGatewayPath:
    """openhands + gateway-on + cluster URL → engine_secrets populated."""

    @pytest.mark.asyncio
    async def test_engine_secrets_populated(self, env) -> None:
        frame = await _build_frame(
            env["factory"],
            engine_name="openhands",
            gateway_enabled=True,
            cluster_external_url="http://localhost:8001",
        )
        secrets = frame["engine_secrets"]
        assert "OPENAI_BASE_URL" in secrets
        assert secrets["OPENAI_BASE_URL"] == "http://localhost:8001/api/v1/llm/v1"
        assert "OPENAI_API_KEY" in secrets
        # The token is mint-per-spawn and unrecoverable from the DB
        # hash; just sanity-check it's a non-empty string.
        assert isinstance(secrets["OPENAI_API_KEY"], str)
        assert secrets["OPENAI_API_KEY"]

    @pytest.mark.asyncio
    async def test_token_persisted_for_reverse_proxy_validation(
        self, env
    ) -> None:
        """The mint must land in ``agent_tokens`` so the gateway's
        ``get_current_identity`` middleware can validate it.

        Without this row the agent's request to ``/api/v1/llm/*``
        would 401 even with the token correctly forwarded.
        """
        frame = await _build_frame(
            env["factory"],
            engine_name="openhands",
            gateway_enabled=True,
            cluster_external_url="http://localhost:8001",
        )
        secrets = frame["engine_secrets"]
        agent_id = frame["agent_id"]

        async with env["factory"]() as db:
            tokens = (
                await db.execute(
                    select(AgentToken).where(AgentToken.agent_id == agent_id)
                )
            ).scalars().all()
        assert len(tokens) >= 1, (
            "spawn frame must persist an AgentToken row for the gateway "
            "to validate the OPENAI_API_KEY at /api/v1/llm/*"
        )
        # And the in-frame token has to actually match what reverse
        # proxy will see — secret-equal is hard to assert without the
        # plaintext, so settle for "at least one row exists" here.
        assert secrets["OPENAI_API_KEY"]


class TestNonOpenHandsEnginesUntouched:
    """The whole point of the engine guard: turning gateway on must
    NOT change spawn frames for the three CLI engines."""

    @pytest.mark.parametrize(
        "engine_name", ["claude-code", "codex", "gemini-cli"]
    )
    @pytest.mark.asyncio
    async def test_engine_secrets_empty(self, env, engine_name: str) -> None:
        frame = await _build_frame(
            env["factory"],
            engine_name=engine_name,
            gateway_enabled=True,
            cluster_external_url="http://localhost:8001",
        )
        # The CRITICAL invariant — flipping the gateway on does not
        # silently re-route claude-code/codex/gemini-cli through it.
        assert frame["engine_secrets"] == {}


class TestGatewayDisabledLeavesFrameEmpty:
    @pytest.mark.parametrize(
        "engine_name", ["openhands", "claude-code", "codex", "gemini-cli"]
    )
    @pytest.mark.asyncio
    async def test_engine_secrets_empty_when_gateway_off(
        self, env, engine_name: str
    ) -> None:
        """Gateway feature flag off → every engine, including
        openhands, gets an empty ``engine_secrets``. Pre-#359 default
        behaviour preserved."""
        frame = await _build_frame(
            env["factory"],
            engine_name=engine_name,
            gateway_enabled=False,
            cluster_external_url="http://localhost:8001",
        )
        assert frame["engine_secrets"] == {}

    @pytest.mark.asyncio
    async def test_no_cluster_url_leaves_frame_empty(self, env) -> None:
        """No reachable doorae URL → no point emitting BASE_URL. The
        helper guards on this; this test verifies the lifecycle wires
        the guard correctly."""
        frame = await _build_frame(
            env["factory"],
            engine_name="openhands",
            gateway_enabled=True,
            cluster_external_url=None,
        )
        assert frame["engine_secrets"] == {}
