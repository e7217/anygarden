"""Tests for the orphan sweeper that marks stalled handlers.

The sweeper is the cluster-side backstop: an agent that dies mid
turn, or reconnects and loses its in-memory handler state, never
emits the ``handler_finished`` the cluster is waiting on. After
``threshold_sec`` the sweeper closes those request_ids out with
``handler_orphaned`` so the activity log stays internally
consistent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from anygarden.db.engine import build_session_factory
from anygarden.db.models import ActivityLog, Agent
from anygarden.scheduler.lifecycle import (
    ORPHAN_THRESHOLD_SEC_DEFAULT,
    OrphanedRequest,
    sweep_orphaned_requests,
)


async def _make_agent(db, *, actual_state: str = "idle") -> Agent:
    a = Agent(
        id=str(uuid.uuid4()),
        name="sweeper-test",
        engine="codex",
        actual_state=actual_state,
    )
    db.add(a)
    await db.commit()
    return a


@pytest.mark.asyncio
async def test_started_without_finished_is_marked_orphaned(engine, db):
    factory = build_session_factory(engine)
    agent = await _make_agent(db)

    old_ts = datetime.now(timezone.utc) - timedelta(seconds=1500)
    req_id = "req-orphan"
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_started",
            request_id=req_id,
            timestamp=old_ts,
            details={"room_id": "r1"},
        )
    )
    await db.commit()

    n = await sweep_orphaned_requests(factory, threshold_sec=1200)
    # #427/#481 — returns the orphaned requests as OrphanedRequest rows.
    assert len(n) == 1
    assert isinstance(n[0], OrphanedRequest)
    assert n[0].request_id == req_id
    assert n[0].agent_id == agent.id
    assert n[0].room_id == "r1"

    async with factory() as s:
        events = (
            await s.execute(
                select(ActivityLog.event_type).where(
                    ActivityLog.request_id == req_id
                )
            )
        ).scalars().all()
    assert "handler_orphaned" in events


@pytest.mark.asyncio
async def test_finished_request_is_not_orphaned(engine, db):
    factory = build_session_factory(engine)
    agent = await _make_agent(db)

    old_ts = datetime.now(timezone.utc) - timedelta(seconds=1500)
    req_id = "req-ok"
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_started",
            request_id=req_id,
            timestamp=old_ts,
            details={"room_id": "r1"},
        )
    )
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_finished",
            request_id=req_id,
            timestamp=old_ts + timedelta(seconds=10),
            details={"room_id": "r1", "outcome": "ok"},
        )
    )
    await db.commit()

    n = await sweep_orphaned_requests(factory, threshold_sec=1200)
    assert len(n) == 0  # #427 — sweep now returns the orphaned request_ids

    async with factory() as s:
        events = (
            await s.execute(
                select(ActivityLog.event_type).where(
                    ActivityLog.request_id == req_id
                )
            )
        ).scalars().all()
    assert "handler_orphaned" not in events


@pytest.mark.asyncio
async def test_already_orphaned_is_not_flagged_twice(engine, db):
    factory = build_session_factory(engine)
    agent = await _make_agent(db)

    old_ts = datetime.now(timezone.utc) - timedelta(seconds=1500)
    req_id = "req-twice"
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_started",
            request_id=req_id,
            timestamp=old_ts,
            details={"room_id": "r1"},
        )
    )
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_orphaned",
            request_id=req_id,
            timestamp=old_ts + timedelta(seconds=20),
            details={"room_id": "r1"},
        )
    )
    await db.commit()

    n = await sweep_orphaned_requests(factory, threshold_sec=1200)
    assert len(n) == 0  # #427 — sweep now returns the orphaned request_ids

    async with factory() as s:
        count = (
            await s.execute(
                select(ActivityLog).where(
                    ActivityLog.request_id == req_id,
                    ActivityLog.event_type == "handler_orphaned",
                )
            )
        ).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_recent_request_below_threshold_is_not_orphaned(engine, db):
    """A request that started 30 s ago isn't orphan-eligible — the
    agent is presumably still working on it, or the engine timeout
    itself will fire shortly."""
    factory = build_session_factory(engine)
    agent = await _make_agent(db)

    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    req_id = "req-young"
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_started",
            request_id=req_id,
            timestamp=recent,
            details={"room_id": "r1"},
        )
    )
    await db.commit()

    n = await sweep_orphaned_requests(factory, threshold_sec=1200)
    assert len(n) == 0  # #427 — sweep now returns the orphaned request_ids


@pytest.mark.asyncio
async def test_null_request_id_rows_are_ignored(engine, db):
    """Legacy ``processing_started`` rows (pre-#204) have
    ``request_id=NULL`` and must not be swept."""
    factory = build_session_factory(engine)
    agent = await _make_agent(db)

    old_ts = datetime.now(timezone.utc) - timedelta(seconds=1500)
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="processing_started",
            request_id=None,
            timestamp=old_ts,
            details={"room_id": "r1"},
        )
    )
    await db.commit()

    n = await sweep_orphaned_requests(factory, threshold_sec=1200)
    assert len(n) == 0  # #427 — sweep now returns the orphaned request_ids


@pytest.mark.asyncio
async def test_crashed_agent_request_is_orphaned_below_threshold(engine, db):
    """#481 fast path — a *recent* (below-threshold) in-flight request
    whose agent has been flipped to ``crashed`` is orphaned immediately,
    without waiting out the slow threshold."""
    factory = build_session_factory(engine)
    agent = await _make_agent(db, actual_state="crashed")

    # Started just 30 s ago — far below the 1200 s slow threshold.
    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    req_id = "req-crashed"
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_started",
            request_id=req_id,
            timestamp=recent,
            details={"room_id": "r-crash"},
        )
    )
    await db.commit()

    n = await sweep_orphaned_requests(factory, threshold_sec=1200)
    assert len(n) == 1
    assert n[0].request_id == req_id
    assert n[0].agent_id == agent.id
    assert n[0].room_id == "r-crash"

    async with factory() as s:
        events = (
            await s.execute(
                select(ActivityLog.event_type).where(
                    ActivityLog.request_id == req_id
                )
            )
        ).scalars().all()
    assert "handler_orphaned" in events


@pytest.mark.asyncio
async def test_live_agent_recent_request_not_orphaned(engine, db):
    """The fast path is gated on ``crashed`` — a recent request on a
    healthy (``running``) agent must still NOT be orphaned (no false
    positive against a live, slow turn)."""
    factory = build_session_factory(engine)
    agent = await _make_agent(db, actual_state="running")

    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    req_id = "req-live-recent"
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_started",
            request_id=req_id,
            timestamp=recent,
            details={"room_id": "r-live"},
        )
    )
    await db.commit()

    n = await sweep_orphaned_requests(factory, threshold_sec=1200)
    assert len(n) == 0


@pytest.mark.asyncio
async def test_crashed_agent_finished_request_not_orphaned(engine, db):
    """Even on a crashed agent, a request that already has a terminal
    event must not be re-orphaned (the HAVING idempotency still holds)."""
    factory = build_session_factory(engine)
    agent = await _make_agent(db, actual_state="crashed")

    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    req_id = "req-crashed-done"
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_started",
            request_id=req_id,
            timestamp=recent,
            details={"room_id": "r"},
        )
    )
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_finished",
            request_id=req_id,
            timestamp=recent + timedelta(seconds=5),
            details={"room_id": "r", "outcome": "ok"},
        )
    )
    await db.commit()

    n = await sweep_orphaned_requests(factory, threshold_sec=1200)
    assert len(n) == 0


def test_default_threshold_matches_design():
    """Keep the default in sync with the design doc (20 min, i.e.
    engine_timeout 15 min + 5 min slack)."""
    assert ORPHAN_THRESHOLD_SEC_DEFAULT == 1200
