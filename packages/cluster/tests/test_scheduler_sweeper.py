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
    """Keep the default in sync with the design doc (20 min). #892 — it
    only applies once the agent stops signalling a live turn."""
    assert ORPHAN_THRESHOLD_SEC_DEFAULT == 1200


async def _old_started(db, agent, req_id, room_id="r-892"):
    db.add(
        ActivityLog(
            agent_id=agent.id,
            event_type="handler_started",
            request_id=req_id,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=1500),
            details={"room_id": room_id},
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_live_turn_past_threshold_is_not_orphaned(engine, db):
    """#892 — a turn older than the threshold whose agent still sends its
    typing keepalive in the room is a long, live turn: not orphaned."""
    factory = build_session_factory(engine)
    agent = await _make_agent(db, actual_state="running")
    await _old_started(db, agent, "req-long-live")
    seen = []

    async def is_alive(agent_id, room_id):
        seen.append((agent_id, room_id))
        return True

    n = await sweep_orphaned_requests(factory, threshold_sec=1200, is_alive=is_alive)
    assert n == []
    assert seen == [(agent.id, "r-892")]
    async with factory() as s:
        events = (
            await s.execute(
                select(ActivityLog.event_type).where(
                    ActivityLog.request_id == "req-long-live"
                )
            )
        ).scalars().all()
    assert "handler_orphaned" not in events


@pytest.mark.asyncio
async def test_turn_is_orphaned_once_keepalive_stops(engine, db):
    """#892 — the same turn is orphaned on the first sweep after the
    keepalive stops (sync callbacks are accepted too)."""
    factory = build_session_factory(engine)
    agent = await _make_agent(db, actual_state="running")
    await _old_started(db, agent, "req-went-quiet")
    alive = True

    n = await sweep_orphaned_requests(
        factory, threshold_sec=1200, is_alive=lambda *_: alive
    )
    assert n == []
    alive = False
    n = await sweep_orphaned_requests(
        factory, threshold_sec=1200, is_alive=lambda *_: alive
    )
    assert [o.request_id for o in n] == ["req-went-quiet"]


@pytest.mark.asyncio
async def test_crashed_agent_is_orphaned_even_if_alive_signal(engine, db):
    """#892 — the fast path (crashed agent) ignores ``is_alive``."""
    factory = build_session_factory(engine)
    agent = await _make_agent(db, actual_state="crashed")
    await _old_started(db, agent, "req-crashed-typing")

    n = await sweep_orphaned_requests(
        factory, threshold_sec=1200, is_alive=lambda *_: True
    )
    assert [o.request_id for o in n] == ["req-crashed-typing"]


@pytest.mark.asyncio
async def test_failing_is_alive_falls_back_to_threshold(engine, db):
    factory = build_session_factory(engine)
    agent = await _make_agent(db, actual_state="running")
    await _old_started(db, agent, "req-alive-error")

    def boom(*_):
        raise RuntimeError("tracker unavailable")

    n = await sweep_orphaned_requests(factory, threshold_sec=1200, is_alive=boom)
    assert [o.request_id for o in n] == ["req-alive-error"]


@pytest.mark.asyncio
async def test_app_turn_liveness_reads_the_typing_tracker(engine, db):
    """#892 — the app's ``is_alive`` maps the agent to its participant in
    the room and asks the typing tracker whether it is still typing."""
    from types import SimpleNamespace

    from anygarden.app import _turn_liveness
    from anygarden.db.models import Participant, Project, Room
    from anygarden.orchestration.rules import TypingTracker

    project = Project(name="p-892")
    db.add(project)
    await db.flush()
    room = Room(project_id=project.id, name="r-892")
    other = Room(project_id=project.id, name="other-892")
    db.add_all([room, other])
    await db.flush()
    agent = await _make_agent(db, actual_state="running")
    part = Participant(room_id=room.id, agent_id=agent.id)
    db.add(part)
    await db.commit()

    tracker = TypingTracker(ttl_seconds=5.0)
    app = SimpleNamespace(
        state=SimpleNamespace(
            typing_tracker=tracker, session_factory=build_session_factory(engine)
        )
    )
    is_alive = _turn_liveness(app)

    assert await is_alive(agent.id, room.id) is False
    tracker.set_typing(room.id, part.id, True)
    assert await is_alive(agent.id, room.id) is True
    # Typing in one room says nothing about another room.
    assert await is_alive(agent.id, other.id) is False
    tracker.set_typing(room.id, part.id, False)
    assert await is_alive(agent.id, room.id) is False
