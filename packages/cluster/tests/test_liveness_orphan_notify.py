"""Tests for the liveness watchdog's orphan visibility + recovery (#481).

``notify_and_redispatch_orphans`` takes the ``OrphanedRequest`` rows the
sweep just produced and, for each:

1. posts a one-line Korean room *system notice* (participant_id=None,
   ``metadata.system_origin == "liveness_orphan"``) and broadcasts it, and
2. re-dispatches the request's assignment Task once (reusing the bounded
   ``_redispatch_task_by_request_id`` core) — but only for mapped
   (assignment) turns; a live turn (no ``AgentTurnTask`` row) gets only the
   notice.

The whole helper is fail-soft: one bad row never blocks the others.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from anygarden.db.engine import build_session_factory
from anygarden.db.models import (
    Agent,
    AgentTurnTask,
    Message,
    Participant,
    Project,
    Room,
    Task,
)
from anygarden.ws.protocol import MessageOut
from anygarden.scheduler.lifecycle import (
    OrphanedRequest,
    notify_and_redispatch_orphans,
)


class _FakeManager:
    """Captures broadcast invocations for unit-testing without a real WS."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def broadcast(self, room_id, frame, **_kwargs):  # noqa: D401
        self.calls.append((room_id, frame))


async def _seed_assignment_turn(db) -> tuple[Room, Task, str]:
    """Seed a room + agent assignee + unresolved Task + AgentTurnTask row.

    Returns ``(room, task, request_id)``. Commits so the helper's own
    sessions (separate sessions on the shared engine) observe the rows.
    """
    project = Project(id=str(uuid.uuid4()), name="ln-proj")
    db.add(project)
    await db.flush()
    room = Room(id=str(uuid.uuid4()), project_id=project.id, name="ln-room")
    agent = Agent(id=str(uuid.uuid4()), name="ln-bot", engine="codex")
    db.add_all([room, agent])
    await db.flush()
    assignee = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add(assignee)
    await db.flush()
    task = Task(
        room_id=room.id,
        title="recover me",
        status="in_progress",
        assignee_participant_id=assignee.id,
        started_at=None,
    )
    db.add(task)
    await db.flush()
    rid = str(uuid.uuid4())
    db.add(
        AgentTurnTask(request_id=rid, task_id=task.id, redispatch_count=0)
    )
    await db.commit()
    return room, task, rid


@pytest.mark.asyncio
async def test_assignment_orphan_gets_notice_and_redispatch(db, engine):
    """A mapped (assignment) orphan posts a system notice AND re-dispatches
    the still-unresolved Task once."""
    room, task, rid = await _seed_assignment_turn(db)
    factory = build_session_factory(engine)
    manager = _FakeManager()

    await notify_and_redispatch_orphans(
        factory,
        manager,
        [OrphanedRequest(request_id=rid, agent_id=None, room_id=room.id)],
    )

    # --- notice persisted as a system message (participant_id None) ---
    msgs = (
        await db.execute(
            select(Message).where(Message.room_id == room.id)
        )
    ).scalars().all()
    notice = next(
        m
        for m in msgs
        if (m.extra_metadata or {}).get("system_origin") == "liveness_orphan"
    )
    assert notice.participant_id is None
    assert notice.extra_metadata.get("request_id") == rid

    # --- Task re-dispatched once: returned to todo + a new mapping row ---
    await db.refresh(task)
    assert task.status == "todo"
    assert task.started_at is None
    assert task.error == "redispatch:liveness_orphan"
    rows = (
        await db.execute(
            select(AgentTurnTask).where(AgentTurnTask.task_id == task.id)
        )
    ).scalars().all()
    counts = sorted(r.redispatch_count for r in rows)
    assert counts == [0, 1]

    # --- both the notice and the re-wake were broadcast on the room ---
    notice_calls = [
        c
        for c in manager.calls
        if isinstance(c[1], MessageOut)
        and (c[1].metadata or {}).get("system_origin") == "liveness_orphan"
    ]
    assert len(notice_calls) == 1
    assert notice_calls[0][0] == room.id


@pytest.mark.asyncio
async def test_live_orphan_gets_notice_only(db, engine):
    """A live (unmapped) orphan gets only the notice — no Task touched,
    no re-dispatch (scope invariant preserved)."""
    project = Project(id=str(uuid.uuid4()), name="p")
    db.add(project)
    await db.flush()
    room = Room(id=str(uuid.uuid4()), project_id=project.id, name="r")
    db.add(room)
    await db.commit()

    factory = build_session_factory(engine)
    manager = _FakeManager()

    await notify_and_redispatch_orphans(
        factory,
        manager,
        [
            OrphanedRequest(
                request_id="live-unmapped", agent_id=None, room_id=room.id
            )
        ],
    )

    msgs = (
        await db.execute(
            select(Message).where(Message.room_id == room.id)
        )
    ).scalars().all()
    # Exactly the one liveness notice — no re-injected assignment mention.
    assert len(msgs) == 1
    assert (msgs[0].extra_metadata or {}).get("system_origin") == (
        "liveness_orphan"
    )
    # No AgentTurnTask was minted (no assignment to recover).
    mappings = (
        await db.execute(select(AgentTurnTask))
    ).scalars().all()
    assert mappings == []


@pytest.mark.asyncio
async def test_one_bad_row_does_not_block_the_rest(db, engine):
    """A row whose room_id is unknown (notice skipped) and which maps to
    nothing must not stop a subsequent good assignment row from being
    surfaced + recovered — full fail-soft."""
    room, task, rid = await _seed_assignment_turn(db)
    factory = build_session_factory(engine)
    manager = _FakeManager()

    rows = [
        # First row: no room_id → notice skipped, no mapping → no redispatch.
        OrphanedRequest(request_id="orphan-no-room", agent_id=None, room_id=None),
        # Second row: a real assignment orphan that must still be handled.
        OrphanedRequest(request_id=rid, agent_id=None, room_id=room.id),
    ]
    await notify_and_redispatch_orphans(factory, manager, rows)

    # The good row was fully processed despite the bad one preceding it.
    await db.refresh(task)
    assert task.status == "todo"
    msgs = (
        await db.execute(
            select(Message).where(Message.room_id == room.id)
        )
    ).scalars().all()
    assert any(
        (m.extra_metadata or {}).get("system_origin") == "liveness_orphan"
        for m in msgs
    )


@pytest.mark.asyncio
async def test_no_manager_skips_notice_still_redispatches(db, engine):
    """When no ConnectionManager is wired (stripped-down app), the notice
    broadcast is skipped gracefully but recovery still runs."""
    room, task, rid = await _seed_assignment_turn(db)
    factory = build_session_factory(engine)

    await notify_and_redispatch_orphans(
        factory,
        None,
        [OrphanedRequest(request_id=rid, agent_id=None, room_id=room.id)],
    )

    # No system notice persisted (broadcast path is manager-gated)...
    msgs = (
        await db.execute(
            select(Message).where(
                Message.room_id == room.id,
            )
        )
    ).scalars().all()
    assert not any(
        (m.extra_metadata or {}).get("system_origin") == "liveness_orphan"
        for m in msgs
    )
    # ...but the Task was still re-dispatched.
    await db.refresh(task)
    assert task.status == "todo"
