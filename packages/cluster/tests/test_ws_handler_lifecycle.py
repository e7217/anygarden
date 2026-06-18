"""Unit tests for the cluster-side LifecycleFrame persist helper.

The full fan-out path (user send → per-agent request_id → tailored
broadcast) is covered by the higher-level WebSocket integration
tests; this file pins the ``_persist_lifecycle_event`` contract
since it is the single point where agent-emitted lifecycle frames
cross into the ActivityLog table.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from anygarden.db.models import ActivityLog, Agent
from anygarden.ws.handler import _persist_lifecycle_event
from anygarden.ws.protocol import LifecycleFrame


async def _make_agent(db) -> Agent:
    a = Agent(
        id=str(uuid.uuid4()),
        name="lifecycle-test",
        engine="codex",
    )
    db.add(a)
    await db.commit()
    return a


@pytest.mark.asyncio
async def test_handler_started_is_persisted(db):
    agent = await _make_agent(db)
    frame = LifecycleFrame(
        request_id="req-xyz",
        room_id="room-1",
        event="handler_started",
    )
    await _persist_lifecycle_event(db, agent_id=agent.id, frame=frame)
    await db.commit()

    rows = (await db.execute(
        select(ActivityLog).where(ActivityLog.agent_id == agent.id)
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "handler_started"
    assert row.request_id == "req-xyz"
    # #447 — no outcome/engine on a started event; columns stay null.
    assert row.outcome is None
    assert row.engine is None
    assert row.details == {"room_id": "room-1"}


@pytest.mark.asyncio
async def test_engine_call_finished_carries_full_details(db):
    agent = await _make_agent(db)
    frame = LifecycleFrame(
        request_id="req-1",
        room_id="room-1",
        event="engine_call_finished",
        engine="codex",
        outcome="timeout",
        duration_ms=900_000,
        error="engine exceeded 900s",
    )
    await _persist_lifecycle_event(db, agent_id=agent.id, frame=frame)
    await db.commit()

    row = (await db.execute(
        select(ActivityLog).where(ActivityLog.agent_id == agent.id)
    )).scalars().one()
    assert row.event_type == "engine_call_finished"
    assert row.request_id == "req-1"
    # #447 — outcome/engine promoted to first-class indexed columns.
    assert row.outcome == "timeout"
    assert row.engine == "codex"
    assert row.details == {
        "room_id": "room-1",
        "engine": "codex",
        "outcome": "timeout",
        "duration_ms": 900_000,
        "error": "engine exceeded 900s",
    }


@pytest.mark.asyncio
async def test_turn_io_fields_are_not_persisted_to_activitylog(db):
    # #433 — prompt/completion ride the engine_call_finished frame for
    # tracing only; ``_lifecycle_details`` selects fields explicitly, so
    # the ActivityLog row must stay metadata-only (no message text in DB).
    agent = await _make_agent(db)
    frame = LifecycleFrame(
        request_id="req-io",
        room_id="room-1",
        event="engine_call_finished",
        engine="codex",
        outcome="ok",
        duration_ms=10,
        prompt="the augmented input the agent sent",
        completion="the engine reply",
    )
    await _persist_lifecycle_event(db, agent_id=agent.id, frame=frame)
    await db.commit()

    row = (await db.execute(
        select(ActivityLog).where(ActivityLog.agent_id == agent.id)
    )).scalars().one()
    assert "prompt" not in row.details
    assert "completion" not in row.details
    assert row.details == {
        "room_id": "room-1",
        "engine": "codex",
        "outcome": "ok",
        "duration_ms": 10,
    }


@pytest.mark.asyncio
async def test_handler_finished_rejected_without_engine_fields(db):
    """Rejected rows carry outcome but no engine/duration/error path.

    ``RoomHandlerSupervisor.dispatch`` emits this shape when a second
    concurrent handler tries to start while the room lock is already
    held — the engine never ran, so engine-specific fields are
    absent.
    """
    agent = await _make_agent(db)
    frame = LifecycleFrame(
        request_id="req-reject",
        room_id="room-1",
        event="handler_finished",
        outcome="rejected",
        error="room busy with request_id=req-prev",
    )
    await _persist_lifecycle_event(db, agent_id=agent.id, frame=frame)
    await db.commit()

    row = (await db.execute(
        select(ActivityLog).where(ActivityLog.agent_id == agent.id)
    )).scalars().one()
    assert row.event_type == "handler_finished"
    assert row.details == {
        "room_id": "room-1",
        "outcome": "rejected",
        "error": "room busy with request_id=req-prev",
    }
    # engine / duration_ms omitted from the payload
    assert "engine" not in row.details
    assert "duration_ms" not in row.details
