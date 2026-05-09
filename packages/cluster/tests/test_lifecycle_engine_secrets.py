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


# ── Issue #369 — token cache stops orphan rows on rebuild ──────────


class TestTokenCachedAcrossRebuilds:
    """Locks the regression where every ``_build_sync_frame`` call
    minted a fresh token. The agent process reads its
    ``OPENAI_API_KEY`` once at spawn; pre-#369 a rebuild path that
    rolled back its transaction (broadcast snapshot, sync_batch
    tick) updated the manifest_store cache with a token whose
    agent_tokens row never committed → permanent 401."""

    @pytest.mark.asyncio
    async def test_repeated_build_returns_same_token(self, env) -> None:
        bus = MachineBus()
        lifecycle = AgentLifecycle(
            db_factory=env["factory"],
            machine_bus=bus,
            cluster_external_url="http://localhost:8001",
            llm_gateway_enabled=True,
        )
        async with env["factory"]() as db:
            agent = Agent(
                engine="openhands",
                name="oh-cache-test",
                desired_state="idle",
                actual_state="idle",
            )
            db.add(agent)
            await db.commit()
            await db.refresh(agent)
            agent_id = agent.id

            # Three back-to-back rebuilds — broadcast / sync_batch
            # ticks land here. All three must hand the same token to
            # the spawn frame.
            f1 = await lifecycle._build_sync_frame(db, agent, rooms=[])
            f2 = await lifecycle._build_sync_frame(db, agent, rooms=[])
            f3 = await lifecycle._build_sync_frame(db, agent, rooms=[])
            await db.commit()

        t1 = f1["engine_secrets"].get("OPENAI_API_KEY")
        t2 = f2["engine_secrets"].get("OPENAI_API_KEY")
        t3 = f3["engine_secrets"].get("OPENAI_API_KEY")
        assert t1 and t2 and t3
        assert t1 == t2 == t3, (
            "_build_sync_frame must reuse the cached doorae_token; "
            "minting per-call leaves orphaned rows that 401 the agent."
        )

        # And the AgentToken DB row count must be 1 — not 3.
        from sqlalchemy import func, select as _sel

        async with env["factory"]() as db2:
            count = (
                await db2.execute(
                    _sel(func.count()).select_from(AgentToken).where(
                        AgentToken.agent_id == agent_id
                    )
                )
            ).scalar_one()
        assert count == 1, (
            f"expected exactly 1 AgentToken row for the agent across "
            f"3 frame rebuilds, got {count}"
        )

    @pytest.mark.asyncio
    async def test_request_stop_evicts_cache(self, env) -> None:
        """Stop must clear the cached token so the next start mints
        fresh. Bypasses ``request_start`` (which needs Machine /
        Participant / room placement scaffolding) and directly tests
        the eviction contract by pre-populating the cache."""
        bus = MachineBus()
        lifecycle = AgentLifecycle(
            db_factory=env["factory"],
            machine_bus=bus,
            cluster_external_url="http://localhost:8001",
            llm_gateway_enabled=True,
        )

        async with env["factory"]() as db:
            agent = Agent(
                engine="openhands",
                name="oh-stop-evict",
                desired_state="running",
                actual_state="running",
            )
            db.add(agent)
            await db.commit()
            await db.refresh(agent)
            agent_id = agent.id

        # Pre-populate the cache as if a prior _build_sync_frame /
        # request_start had landed a token.
        lifecycle._token_cache[agent_id] = "agt_fake_prior_token"
        assert agent_id in lifecycle._token_cache

        await lifecycle.request_stop(agent_id)
        assert agent_id not in lifecycle._token_cache, (
            "request_stop must evict the cached token so the next "
            "start mints fresh."
        )

    def test_evict_token_helper_is_no_op_for_unknown_agent(
        self, env  # noqa: ARG002 — fixture activates AgentLifecycle module
    ) -> None:
        bus = MachineBus()
        lifecycle = AgentLifecycle(
            db_factory=env["factory"],
            machine_bus=bus,
        )
        # Should not raise on unknown agent_id.
        lifecycle.evict_token("does-not-exist")
