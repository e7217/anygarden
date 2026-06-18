"""Unit tests for :mod:`anygarden.budgets.ledger` (#453, Wave 1d).

Drives the window SUM and the hard-stop evaluation directly against an
in-memory DB, seeding ``LLMGatewayUsage`` rows and
``TokenBudgetPolicy`` rows by hand. No FastAPI, no proxy — those are
covered by ``test_llm_gateway_reverse_proxy.py``.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest_asyncio

from anygarden.budgets.ledger import (
    clear_observed_cache,
    compute_observed_tokens,
    evaluate_invocation_block,
)
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    LLMGatewayUsage,
    Project,
    Room,
    TokenBudgetPolicy,
)


@pytest_asyncio.fixture()
async def factory() -> AsyncIterator:
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    fac = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # ``llm_gateway_usage.agent_id`` / ``room_id`` carry FKs and SQLite
    # enforces them, so seed real Agent/Room rows with the fixed ids the
    # tests reference rather than dangling synthetic ones.
    async with fac() as db:
        db.add_all(
            [
                Agent(id="a1", name="A1", engine="claude-code"),
                Agent(id="a2", name="A2", engine="claude-code"),
            ]
        )
        project = Project(id="p1", name="P1")
        db.add(project)
        await db.flush()
        db.add_all(
            [
                Room(id="r1", project_id="p1", name="R1"),
                Room(id="r2", project_id="p1", name="R2"),
            ]
        )
        await db.commit()
    # Each test starts with a clean process-local TTL cache so cached
    # SUMs from a previous test can't bleed in.
    clear_observed_cache()
    yield fac
    await engine.dispose()
    clear_observed_cache()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _add_usage(
    fac,
    *,
    prompt: int | None,
    completion: int | None,
    status_code: int = 200,
    agent_id: str | None = None,
    room_id: str | None = None,
    ts: datetime | None = None,
) -> None:
    async with fac() as db:
        db.add(
            LLMGatewayUsage(
                identity_kind="agent",
                identity_id=agent_id or "x",
                agent_id=agent_id,
                room_id=room_id,
                model_name="m",
                prompt_tokens=prompt,
                completion_tokens=completion,
                status_code=status_code,
                timestamp=ts or _now(),
            )
        )
        await db.commit()


# ── compute_observed_tokens ────────────────────────────────────────────


async def test_observed_sums_prompt_plus_completion_in_window(factory) -> None:
    await _add_usage(factory, prompt=100, completion=50)
    await _add_usage(factory, prompt=10, completion=5)
    window_start = _now() - timedelta(hours=24)
    async with factory() as db:
        total = await compute_observed_tokens(
            db, scope_type="global", scope_id=None, window_start=window_start
        )
    assert total == 165


async def test_observed_coalesces_null_tokens(factory) -> None:
    # Streaming parse failures leave token columns NULL — they must
    # count as 0, not crash the SUM or undercount via NoneType.
    await _add_usage(factory, prompt=None, completion=None)
    await _add_usage(factory, prompt=40, completion=None)
    await _add_usage(factory, prompt=None, completion=7)
    window_start = _now() - timedelta(hours=24)
    async with factory() as db:
        total = await compute_observed_tokens(
            db, scope_type="global", scope_id=None, window_start=window_start
        )
    assert total == 47


async def test_observed_excludes_status_400_and_above(factory) -> None:
    # The critical self-perpetuation guard: refusal/error rows (incl. the
    # 429s this feature writes) must NOT inflate the observed sum.
    await _add_usage(factory, prompt=100, completion=0, status_code=200)
    await _add_usage(factory, prompt=999, completion=999, status_code=429)
    await _add_usage(factory, prompt=500, completion=0, status_code=502)
    await _add_usage(factory, prompt=1, completion=0, status_code=399)
    window_start = _now() - timedelta(hours=24)
    async with factory() as db:
        total = await compute_observed_tokens(
            db, scope_type="global", scope_id=None, window_start=window_start
        )
    assert total == 101  # 100 + 1, 429/502 excluded


async def test_observed_excludes_rows_before_window(factory) -> None:
    await _add_usage(factory, prompt=50, completion=0, ts=_now())
    await _add_usage(
        factory, prompt=999, completion=0, ts=_now() - timedelta(hours=48)
    )
    window_start = _now() - timedelta(hours=24)
    async with factory() as db:
        total = await compute_observed_tokens(
            db, scope_type="global", scope_id=None, window_start=window_start
        )
    assert total == 50


async def test_observed_scope_matching_agent_and_room(factory) -> None:
    await _add_usage(factory, prompt=10, completion=0, agent_id="a1", room_id="r1")
    await _add_usage(factory, prompt=20, completion=0, agent_id="a2", room_id="r1")
    await _add_usage(factory, prompt=5, completion=0, agent_id="a1", room_id="r2")
    window_start = _now() - timedelta(hours=24)
    async with factory() as db:
        agent_total = await compute_observed_tokens(
            db, scope_type="agent", scope_id="a1", window_start=window_start
        )
        room_total = await compute_observed_tokens(
            db, scope_type="room", scope_id="r1", window_start=window_start
        )
        global_total = await compute_observed_tokens(
            db, scope_type="global", scope_id=None, window_start=window_start
        )
    assert agent_total == 15  # a1: 10 + 5
    assert room_total == 30  # r1: 10 + 20
    assert global_total == 35


# ── evaluate_invocation_block ──────────────────────────────────────────


async def _add_policy(
    fac,
    *,
    scope_type: str,
    scope_id: str | None,
    ceiling: int,
    hard_stop: bool = True,
    is_active: bool = True,
) -> None:
    async with fac() as db:
        db.add(
            TokenBudgetPolicy(
                scope_type=scope_type,
                scope_id=scope_id,
                token_ceiling=ceiling,
                hard_stop_enabled=hard_stop,
                is_active=is_active,
            )
        )
        await db.commit()


async def test_block_when_observed_at_or_over_ceiling(factory) -> None:
    await _add_usage(factory, prompt=100, completion=0, agent_id="a1")
    await _add_policy(factory, scope_type="agent", scope_id="a1", ceiling=100)
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id=None
    )
    assert block is not None
    assert block.scope_type == "agent"
    assert block.scope_id == "a1"


async def test_no_block_when_under_ceiling(factory) -> None:
    await _add_usage(factory, prompt=99, completion=0, agent_id="a1")
    await _add_policy(factory, scope_type="agent", scope_id="a1", ceiling=100)
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id=None
    )
    assert block is None


async def test_no_block_when_no_policy(factory) -> None:
    # THE default-OFF invariant at the ledger layer: an over-spending
    # agent with no policy is never blocked.
    await _add_usage(factory, prompt=10_000, completion=10_000, agent_id="a1")
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id="r1"
    )
    assert block is None


async def test_no_block_when_policy_inactive(factory) -> None:
    await _add_usage(factory, prompt=500, completion=0, agent_id="a1")
    await _add_policy(
        factory, scope_type="agent", scope_id="a1", ceiling=100, is_active=False
    )
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id=None
    )
    assert block is None


async def test_no_block_when_hard_stop_disabled(factory) -> None:
    # The kill-switch default: a policy that is active but not
    # hard_stop_enabled never refuses (it's a warn/observe-only policy).
    await _add_usage(factory, prompt=500, completion=0, agent_id="a1")
    await _add_policy(
        factory, scope_type="agent", scope_id="a1", ceiling=100, hard_stop=False
    )
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id=None
    )
    assert block is None


async def test_global_policy_blocks_any_caller(factory) -> None:
    await _add_usage(factory, prompt=200, completion=0, agent_id="a1")
    await _add_policy(factory, scope_type="global", scope_id=None, ceiling=150)
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id=None
    )
    assert block is not None
    assert block.scope_type == "global"


async def test_room_only_evaluated_when_room_id_known(factory) -> None:
    await _add_usage(factory, prompt=300, completion=0, room_id="r1")
    await _add_policy(factory, scope_type="room", scope_id="r1", ceiling=100)
    # room_id unknown (tracing off) → room policy not evaluated, no block.
    assert (
        await evaluate_invocation_block(factory, agent_id="a1", room_id=None)
    ) is None
    # room_id known → block fires.
    clear_observed_cache()
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id="r1"
    )
    assert block is not None
    assert block.scope_type == "room"


async def test_global_reported_before_agent_when_both_tripped(factory) -> None:
    await _add_usage(factory, prompt=1000, completion=0, agent_id="a1")
    await _add_policy(factory, scope_type="global", scope_id=None, ceiling=100)
    await _add_policy(factory, scope_type="agent", scope_id="a1", ceiling=100)
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id=None
    )
    assert block is not None
    # Deterministic order: global before agent.
    assert block.scope_type == "global"


# ── TTL cache behaviour ────────────────────────────────────────────────


async def test_ttl_cache_serves_stale_sum_until_expiry(factory) -> None:
    """Within the TTL, a second evaluation reuses the cached SUM and does
    not observe usage added after the first read — proving the cache is
    in play (and that a few seconds of slop is the accepted trade-off)."""
    await _add_usage(factory, prompt=50, completion=0, agent_id="a1")
    await _add_policy(factory, scope_type="agent", scope_id="a1", ceiling=100)

    # First eval: observed=50 < 100 → no block, caches 50.
    assert (
        await evaluate_invocation_block(factory, agent_id="a1", room_id=None)
    ) is None

    # Push observed well over the ceiling.
    await _add_usage(factory, prompt=500, completion=0, agent_id="a1")

    # Still within TTL → cache serves the stale 50 → no block yet.
    assert (
        await evaluate_invocation_block(factory, agent_id="a1", room_id=None)
    ) is None

    # Clearing the cache (mimics TTL expiry) → fresh SUM=550 → block.
    clear_observed_cache()
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id=None
    )
    assert block is not None
    assert block.scope_type == "agent"
