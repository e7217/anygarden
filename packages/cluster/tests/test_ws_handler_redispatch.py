"""Tests for the lifecycle→Task re-dispatch bridge (#463, reliability Wave 2).

An *assignment-originated* turn — woken by a synthetic ``[TASK]`` mention
injected through ``inject_task_assignment_message`` — mints a server-side
``request_id`` and an ``AgentTurnTask`` row correlating it with the Task.
When that turn's ``handler_finished`` frame returns a terminal non-ok
outcome (``rejected`` / ``timeout`` / ``failed``) the WS handler
(``_maybe_redispatch_task``) returns the still-unresolved Task to ``todo``
and re-injects the assignment mention once. The flip-loop is bounded by the
carried ``redispatch_count`` (``_MAX_TASK_REDISPATCH`` = 1).

Core scope invariant: only mapped (assignment) turns are ever touched.
A live (user-send / peer-handoff) turn writes no ``AgentTurnTask`` row, so
the bridge leaves it completely alone.
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
    User,
)
from anygarden.messages.service import inject_task_assignment_message
from anygarden.ws.handler import _MAX_TASK_REDISPATCH, _maybe_redispatch_task
from anygarden.ws.protocol import LifecycleFrame, MessageOut


class _FakeManager:
    """Captures broadcast invocations for unit-testing without a real WS."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def broadcast(self, room_id, frame, **_kwargs):  # noqa: D401
        self.calls.append((room_id, frame))


async def _seed_assignment_turn(
    db,
    *,
    task_status: str = "in_progress",
    redispatch_count: int = 0,
) -> tuple[Room, Task, Participant, str]:
    """Seed a room with an agent assignee + a Task + an AgentTurnTask row.

    Returns ``(room, task, assignee_participant, request_id)``. Commits so
    the helper's own session (a separate session on the shared engine) can
    observe the rows. The ``request_id`` is the minted turn correlation id
    written into ``agent_turn_tasks``.
    """
    project = Project(id=str(uuid.uuid4()), name="rd-proj")
    db.add(project)
    await db.flush()
    room = Room(id=str(uuid.uuid4()), project_id=project.id, name="rd-room")
    user = User(email=f"creator-{uuid.uuid4()}@example.com", password_hash="x")
    agent = Agent(id=str(uuid.uuid4()), name="rd-bot", engine="codex")
    db.add_all([room, user, agent])
    await db.flush()
    assignee = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add(assignee)
    await db.flush()
    task = Task(
        room_id=room.id,
        title="recover me",
        status=task_status,
        assignee_participant_id=assignee.id,
        started_at=None,
    )
    db.add(task)
    await db.flush()
    rid = str(uuid.uuid4())
    db.add(
        AgentTurnTask(
            request_id=rid,
            task_id=task.id,
            redispatch_count=redispatch_count,
        )
    )
    await db.commit()
    return room, task, assignee, rid


def _terminal_frame(room_id: str, rid: str, outcome: str) -> LifecycleFrame:
    return LifecycleFrame(
        request_id=rid,
        room_id=room_id,
        event="handler_finished",
        outcome=outcome,  # type: ignore[arg-type]
        duration_ms=10,
    )


# ── inject now mints request_id + writes the mapping ─────────────────


@pytest.mark.asyncio
async def test_inject_mints_request_id_and_writes_mapping(db):
    """#463 — ``inject_task_assignment_message`` stamps a ``request_id`` on
    the message metadata and persists a matching ``AgentTurnTask`` row."""
    project = Project(id=str(uuid.uuid4()), name="p")
    db.add(project)
    await db.flush()
    room = Room(id=str(uuid.uuid4()), project_id=project.id, name="r")
    agent = Agent(id=str(uuid.uuid4()), name="bot", engine="codex")
    db.add_all([room, agent])
    await db.flush()
    assignee = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add(assignee)
    await db.flush()
    task = Task(
        room_id=room.id,
        title="t",
        status="todo",
        assignee_participant_id=assignee.id,
    )
    db.add(task)
    await db.flush()

    msg = await inject_task_assignment_message(
        db, room=room, task=task, sender_participant_id=None
    )

    # request_id is stamped onto the metadata under the live-path key.
    assert msg.extra_metadata is not None
    rid = msg.extra_metadata.get("request_id")
    assert isinstance(rid, str) and rid

    # A correlation row links that request_id to the task (count default 0).
    mapping = await db.get(AgentTurnTask, rid)
    assert mapping is not None
    assert mapping.task_id == task.id
    assert mapping.redispatch_count == 0


@pytest.mark.asyncio
async def test_inject_accepts_caller_request_id_and_carries_count(db):
    """The re-dispatch path passes an explicit ``request_id`` /
    ``redispatch_count``; both must be honoured verbatim."""
    project = Project(id=str(uuid.uuid4()), name="p")
    db.add(project)
    await db.flush()
    room = Room(id=str(uuid.uuid4()), project_id=project.id, name="r")
    agent = Agent(id=str(uuid.uuid4()), name="bot", engine="codex")
    db.add_all([room, agent])
    await db.flush()
    assignee = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add(assignee)
    await db.flush()
    task = Task(
        room_id=room.id,
        title="t",
        status="todo",
        assignee_participant_id=assignee.id,
    )
    db.add(task)
    await db.flush()

    forced_rid = "forced-rid-123"
    msg = await inject_task_assignment_message(
        db,
        room=room,
        task=task,
        sender_participant_id=None,
        request_id=forced_rid,
        redispatch_count=1,
    )
    assert msg.extra_metadata["request_id"] == forced_rid
    mapping = await db.get(AgentTurnTask, forced_rid)
    assert mapping is not None
    assert mapping.redispatch_count == 1


# ── re-dispatch hook ─────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["rejected", "timeout", "failed"])
async def test_terminal_non_ok_redispatches_once(db, engine, outcome):
    """A mapped assignment turn whose ``handler_finished`` is terminal-non-ok
    flips its (unresolved) Task back to ``todo`` (started_at None,
    assigned_at refreshed) and re-injects once with redispatch_count=1."""
    room, task, assignee, rid = await _seed_assignment_turn(
        db, task_status="in_progress", redispatch_count=0
    )
    factory = build_session_factory(engine)
    manager = _FakeManager()

    await _maybe_redispatch_task(
        factory, manager=manager, frame=_terminal_frame(room.id, rid, outcome)
    )

    # Task returned to a re-runnable state.
    await db.refresh(task)
    assert task.status == "todo"
    assert task.started_at is None
    assert task.assigned_at is not None
    assert task.error == f"redispatch:{outcome}"

    # A NEW AgentTurnTask row carries redispatch_count=1 (a fresh request_id,
    # distinct from the original).
    rows = (
        await db.execute(
            select(AgentTurnTask).where(AgentTurnTask.task_id == task.id)
        )
    ).scalars().all()
    counts = sorted(r.redispatch_count for r in rows)
    assert counts == [0, 1]
    new_rid = next(r.request_id for r in rows if r.redispatch_count == 1)
    assert new_rid != rid

    # The re-wake was broadcast on the room channel as a MessageOut.
    assert len(manager.calls) == 1
    broadcast_room, frame = manager.calls[0]
    assert broadcast_room == room.id
    assert isinstance(frame, MessageOut)
    assert frame.metadata is not None
    assert frame.metadata.get("request_id") == new_rid


@pytest.mark.asyncio
async def test_second_terminal_failure_does_not_redispatch(db, engine):
    """A turn whose mapping already carries redispatch_count == MAX is NOT
    re-dispatched again — the flip-loop bound."""
    room, task, assignee, rid = await _seed_assignment_turn(
        db, task_status="in_progress", redispatch_count=_MAX_TASK_REDISPATCH
    )
    factory = build_session_factory(engine)
    manager = _FakeManager()

    await _maybe_redispatch_task(
        factory, manager=manager, frame=_terminal_frame(room.id, rid, "timeout")
    )

    # No re-dispatch: no new mapping row, no broadcast. The Task is left as
    # the sweeper / a human will find it (status untouched here).
    rows = (
        await db.execute(
            select(AgentTurnTask).where(AgentTurnTask.task_id == task.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].redispatch_count == _MAX_TASK_REDISPATCH
    assert manager.calls == []
    # No re-injected wake-up message was persisted.
    msgs = (
        await db.execute(select(Message).where(Message.room_id == room.id))
    ).scalars().all()
    assert msgs == []


@pytest.mark.asyncio
async def test_unmapped_live_turn_is_untouched(db, engine):
    """A ``handler_finished`` frame with NO ``AgentTurnTask`` mapping (a live
    user-send / peer-handoff turn) must do nothing — the scope invariant."""
    project = Project(id=str(uuid.uuid4()), name="p")
    db.add(project)
    await db.flush()
    room = Room(id=str(uuid.uuid4()), project_id=project.id, name="r")
    db.add(room)
    await db.commit()

    factory = build_session_factory(engine)
    manager = _FakeManager()

    # request_id that is not in agent_turn_tasks.
    await _maybe_redispatch_task(
        factory,
        manager=manager,
        frame=_terminal_frame(room.id, "live-unmapped-rid", "failed"),
    )

    assert manager.calls == []
    msgs = (
        await db.execute(select(Message).where(Message.room_id == room.id))
    ).scalars().all()
    assert msgs == []


@pytest.mark.asyncio
async def test_ok_outcome_does_not_redispatch(db, engine):
    """A successful (``ok``) handler_finished — even on a mapped turn — is
    not a delivery failure and must not re-dispatch."""
    room, task, assignee, rid = await _seed_assignment_turn(
        db, task_status="in_progress", redispatch_count=0
    )
    factory = build_session_factory(engine)
    manager = _FakeManager()

    await _maybe_redispatch_task(
        factory, manager=manager, frame=_terminal_frame(room.id, rid, "ok")
    )

    await db.refresh(task)
    assert task.status == "in_progress"  # untouched
    assert manager.calls == []
    rows = (
        await db.execute(
            select(AgentTurnTask).where(AgentTurnTask.task_id == task.id)
        )
    ).scalars().all()
    assert len(rows) == 1  # no new mapping


@pytest.mark.asyncio
async def test_resolved_task_is_not_redispatched(db, engine):
    """A mapped turn whose Task is already resolved (``done``) is not
    re-dispatched even on a terminal-non-ok frame — nothing to recover."""
    room, task, assignee, rid = await _seed_assignment_turn(
        db, task_status="done", redispatch_count=0
    )
    factory = build_session_factory(engine)
    manager = _FakeManager()

    await _maybe_redispatch_task(
        factory, manager=manager, frame=_terminal_frame(room.id, rid, "failed")
    )

    await db.refresh(task)
    assert task.status == "done"  # untouched
    assert manager.calls == []
    rows = (
        await db.execute(
            select(AgentTurnTask).where(AgentTurnTask.task_id == task.id)
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_cancelled_outcome_does_not_redispatch(db, engine):
    """``cancelled`` is a deliberate stop (budget / shutdown), not a delivery
    failure, so it must not re-dispatch (would fight the canceller)."""
    room, task, assignee, rid = await _seed_assignment_turn(
        db, task_status="in_progress", redispatch_count=0
    )
    factory = build_session_factory(engine)
    manager = _FakeManager()

    await _maybe_redispatch_task(
        factory, manager=manager, frame=_terminal_frame(room.id, rid, "cancelled")
    )

    await db.refresh(task)
    assert task.status == "in_progress"
    assert manager.calls == []
