"""Unit tests for :func:`anygarden.budgets.ledger.evaluate_cost_event`
and the budget-pause short-circuit in ``evaluate_invocation_block``
(#455, Wave 2a).

Drives the post-cost evaluator directly against an in-memory DB with a
mock ``AgentLifecycle``: no FastAPI, no reverse proxy. Asserts the
active-stop vs incident-only split (AGENT scope stops; ROOM / GLOBAL are
incident-only), soft vs hard incidents, dedup / idempotency, and the
default-OFF no-behaviour-change invariant.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest_asyncio
from sqlalchemy import select

from anygarden.budgets.ledger import (
    clear_observed_cache,
    evaluate_cost_event,
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
    TokenBudgetIncident,
    TokenBudgetPolicy,
)


class _MockLifecycle:
    """Records ``request_stop`` / ``request_start`` calls."""

    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.started: list[str] = []

    async def request_stop(self, agent_id: str) -> None:
        self.stopped.append(agent_id)

    async def request_start(self, agent_id: str) -> None:
        self.started.append(agent_id)


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
    completion: int | None = 0,
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


async def _add_policy(
    fac,
    *,
    scope_type: str,
    scope_id: str | None,
    ceiling: int,
    warn_percent: int = 80,
    hard_stop: bool = True,
    is_active: bool = True,
) -> None:
    async with fac() as db:
        db.add(
            TokenBudgetPolicy(
                scope_type=scope_type,
                scope_id=scope_id,
                token_ceiling=ceiling,
                warn_percent=warn_percent,
                hard_stop_enabled=hard_stop,
                is_active=is_active,
            )
        )
        await db.commit()


async def _incidents(fac) -> list[TokenBudgetIncident]:
    async with fac() as db:
        return list(
            (await db.execute(select(TokenBudgetIncident))).scalars().all()
        )


async def _pause_reason(fac, agent_id: str) -> str | None:
    async with fac() as db:
        return (
            await db.execute(
                select(Agent.pause_reason).where(Agent.id == agent_id)
            )
        ).scalar_one()


# ── AGENT-scope hard breach → stop + pause + hard incident ─────────────


async def test_agent_hard_breach_stops_and_records_incident(factory) -> None:
    await _add_usage(factory, prompt=150, agent_id="a1")
    await _add_policy(factory, scope_type="agent", scope_id="a1", ceiling=100)
    lifecycle = _MockLifecycle()

    await evaluate_cost_event(
        factory, agent_id="a1", room_id=None, lifecycle=lifecycle
    )

    assert lifecycle.stopped == ["a1"]
    assert await _pause_reason(factory, "a1") == "budget"
    rows = await _incidents(factory)
    assert len(rows) == 1
    assert rows[0].scope_type == "agent"
    assert rows[0].scope_id == "a1"
    assert rows[0].threshold_type == "hard"
    assert rows[0].status == "open"
    assert rows[0].observed_tokens == 150


# ── ROOM-scope hard breach → incident only, NEVER stop ─────────────────


async def test_room_hard_breach_is_incident_only(factory) -> None:
    await _add_usage(factory, prompt=300, agent_id="a1", room_id="r1")
    await _add_policy(factory, scope_type="room", scope_id="r1", ceiling=100)
    lifecycle = _MockLifecycle()

    await evaluate_cost_event(
        factory, agent_id="a1", room_id="r1", lifecycle=lifecycle
    )

    # The collateral-damage guard: room breach never kills an agent.
    assert lifecycle.stopped == []
    assert await _pause_reason(factory, "a1") is None
    rows = await _incidents(factory)
    assert len(rows) == 1
    assert rows[0].scope_type == "room"
    assert rows[0].threshold_type == "hard"


async def test_global_hard_breach_is_incident_only(factory) -> None:
    await _add_usage(factory, prompt=500, agent_id="a1", room_id="r1")
    await _add_policy(factory, scope_type="global", scope_id=None, ceiling=100)
    lifecycle = _MockLifecycle()

    await evaluate_cost_event(
        factory, agent_id="a1", room_id="r1", lifecycle=lifecycle
    )

    assert lifecycle.stopped == []
    assert await _pause_reason(factory, "a1") is None
    rows = await _incidents(factory)
    assert len(rows) == 1
    assert rows[0].scope_type == "global"
    assert rows[0].threshold_type == "hard"


# ── SOFT breach → soft incident, no stop ───────────────────────────────


async def test_soft_breach_records_soft_incident_no_stop(factory) -> None:
    # ceiling 100, warn 80 → soft band is [80, 100). observed 90.
    await _add_usage(factory, prompt=90, agent_id="a1")
    await _add_policy(
        factory, scope_type="agent", scope_id="a1", ceiling=100, warn_percent=80
    )
    lifecycle = _MockLifecycle()

    await evaluate_cost_event(
        factory, agent_id="a1", room_id=None, lifecycle=lifecycle
    )

    assert lifecycle.stopped == []
    assert await _pause_reason(factory, "a1") is None
    rows = await _incidents(factory)
    assert len(rows) == 1
    assert rows[0].threshold_type == "soft"
    assert rows[0].status == "open"


# ── Under both thresholds → nothing ────────────────────────────────────


async def test_under_warn_does_nothing(factory) -> None:
    await _add_usage(factory, prompt=50, agent_id="a1")
    await _add_policy(
        factory, scope_type="agent", scope_id="a1", ceiling=100, warn_percent=80
    )
    lifecycle = _MockLifecycle()

    await evaluate_cost_event(
        factory, agent_id="a1", room_id=None, lifecycle=lifecycle
    )

    assert lifecycle.stopped == []
    assert await _pause_reason(factory, "a1") is None
    assert await _incidents(factory) == []


# ── Default-OFF: no policy → no-op (no-behaviour-change invariant) ──────


async def test_default_no_policy_does_nothing(factory) -> None:
    """THE invariant: with no policy at all, even a wildly over-spending
    agent triggers neither an incident nor a stop — merging changes no
    runtime behaviour until an admin enables a hard-stop policy."""
    await _add_usage(factory, prompt=10_000_000, agent_id="a1")
    lifecycle = _MockLifecycle()

    await evaluate_cost_event(
        factory, agent_id="a1", room_id="r1", lifecycle=lifecycle
    )

    assert lifecycle.stopped == []
    assert lifecycle.started == []
    assert await _pause_reason(factory, "a1") is None
    assert await _incidents(factory) == []


async def test_inactive_or_warn_only_policy_does_nothing(factory) -> None:
    # active but hard_stop_enabled=False → not loaded by the evaluator.
    await _add_usage(factory, prompt=500, agent_id="a1")
    await _add_policy(
        factory, scope_type="agent", scope_id="a1", ceiling=100, hard_stop=False
    )
    lifecycle = _MockLifecycle()

    await evaluate_cost_event(
        factory, agent_id="a1", room_id=None, lifecycle=lifecycle
    )

    assert lifecycle.stopped == []
    assert await _incidents(factory) == []


# ── Idempotency / dedup ────────────────────────────────────────────────


async def test_repeated_hard_breach_dedups_incident_and_stop(factory) -> None:
    """Two evaluations over the same window create exactly one incident
    and the second stop call (request_stop re-call) is safe / a no-op."""
    await _add_usage(factory, prompt=150, agent_id="a1")
    await _add_policy(factory, scope_type="agent", scope_id="a1", ceiling=100)
    lifecycle = _MockLifecycle()

    await evaluate_cost_event(
        factory, agent_id="a1", room_id=None, lifecycle=lifecycle
    )
    # Second call: pause_reason already 'budget' → agent not re-stopped;
    # incident already open for (policy, window, hard) → not re-created.
    await evaluate_cost_event(
        factory, agent_id="a1", room_id=None, lifecycle=lifecycle
    )

    rows = await _incidents(factory)
    assert len(rows) == 1
    # First call stops; second call sees pause_reason already set and
    # skips the re-stop (the request_stop path itself is also idempotent).
    assert lifecycle.stopped == ["a1"]


async def test_lifecycle_none_records_incident_without_stop(factory) -> None:
    # No lifecycle wired (e.g. test / startup) → incident only, no crash.
    await _add_usage(factory, prompt=150, agent_id="a1")
    await _add_policy(factory, scope_type="agent", scope_id="a1", ceiling=100)

    await evaluate_cost_event(
        factory, agent_id="a1", room_id=None, lifecycle=None
    )

    rows = await _incidents(factory)
    assert len(rows) == 1
    assert rows[0].threshold_type == "hard"
    # No lifecycle → pause_reason stays NULL (stop is what sets it).
    assert await _pause_reason(factory, "a1") is None


# ── invocation_block short-circuit on a budget-paused agent ────────────


async def test_paused_agent_is_blocked_by_short_circuit(factory) -> None:
    # No policy, no usage — the ONLY reason this blocks is pause_reason.
    async with factory() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == "a1"))
        ).scalar_one()
        agent.pause_reason = "budget"
        await db.commit()

    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id=None
    )
    assert block is not None
    assert block.scope_type == "agent"
    assert block.scope_id == "a1"
    assert "budget" in block.reason


async def test_unpaused_agent_not_blocked_by_short_circuit(factory) -> None:
    # pause_reason NULL (default) → short-circuit does not fire; with no
    # policy the call is not blocked (default-OFF).
    await _add_usage(factory, prompt=10_000, agent_id="a1")
    block = await evaluate_invocation_block(
        factory, agent_id="a1", room_id=None
    )
    assert block is None


async def test_window_excludes_old_usage_for_cost_event(factory) -> None:
    # A breach-sized usage row outside the 24h window must not trip an
    # incident — proves evaluate_cost_event honours the window.
    await _add_usage(
        factory, prompt=10_000, agent_id="a1", ts=_now() - timedelta(hours=48)
    )
    await _add_usage(factory, prompt=10, agent_id="a1", ts=_now())
    await _add_policy(factory, scope_type="agent", scope_id="a1", ceiling=100)
    lifecycle = _MockLifecycle()

    await evaluate_cost_event(
        factory, agent_id="a1", room_id=None, lifecycle=lifecycle
    )

    assert lifecycle.stopped == []
    assert await _incidents(factory) == []
