"""Tests for the stuck-task sweeper (#314).

Covers the contract:
- ``status='todo'`` past ``TASK_PICKUP_TIMEOUT_SECONDS`` → ``failed``
- ``status='in_progress'`` past ``TASK_EXECUTION_TIMEOUT_SECONDS`` → ``failed``
- Goal-derived tasks: ``consecutive_failures`` increments and goal
  flips to ``paused`` once the policy threshold is crossed.
- Manual tasks (``goal_id IS NULL``): status flips but no goal
  bookkeeping (apply_completion is a no-op).
- ``task.updated`` frame is fanned out so the right-rail catches up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from doorae.db.models import Agent, Goal, Participant, Room, Task, User
from doorae.goals.policy import (
    GOAL_FAILURE_PAUSE_THRESHOLD,
    TASK_EXECUTION_TIMEOUT_SECONDS,
    TASK_PICKUP_TIMEOUT_SECONDS,
)
from doorae.goals.sweeper import sweep_stuck_tasks


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _FakeManager:
    """Records broadcast calls so the test can assert fanout occurred
    without bringing up a real WebSocket layer."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def broadcast(self, room_id, frame, **_kwargs):
        self.calls.append((room_id, frame))

    async def push_to_users(self, user_ids, frame):
        # ``fanout_task_event`` also pings admin users; we don't
        # exercise that surface here.
        pass


async def _seed_room_with_agent(db) -> tuple[Room, Participant, Agent, User]:
    user = User(email="u@x", password_hash="x", is_admin=True)
    db.add(user)
    agent = Agent(name="bot", engine="codex")
    db.add(agent)
    room = Room(name="r")
    db.add(room)
    await db.flush()
    p = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add(p)
    await db.flush()
    return room, p, agent, user


# ── Pickup timeout ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_todo_past_pickup_timeout_is_marked_failed(db):
    room, p, _, user = await _seed_room_with_agent(db)
    now = _utcnow()
    task = Task(
        room_id=room.id,
        title="stuck",
        status="todo",
        assignee_participant_id=p.id,
        assigned_at=now - timedelta(seconds=TASK_PICKUP_TIMEOUT_SECONDS + 30),
        created_by=user.id,
    )
    db.add(task)
    await db.commit()

    n = await sweep_stuck_tasks(db, manager=None, now=now)
    await db.commit()
    await db.refresh(task)
    assert n == 1
    assert task.status == "failed"
    assert task.error == "pickup_timeout"


@pytest.mark.asyncio
async def test_todo_under_pickup_threshold_is_left_alone(db):
    room, p, _, user = await _seed_room_with_agent(db)
    now = _utcnow()
    task = Task(
        room_id=room.id,
        title="just-assigned",
        status="todo",
        assignee_participant_id=p.id,
        assigned_at=now - timedelta(seconds=TASK_PICKUP_TIMEOUT_SECONDS - 30),
        created_by=user.id,
    )
    db.add(task)
    await db.commit()

    n = await sweep_stuck_tasks(db, manager=None, now=now)
    await db.refresh(task)
    assert n == 0
    assert task.status == "todo"


@pytest.mark.asyncio
async def test_todo_with_null_assigned_at_is_skipped(db):
    """An unassigned to-do has no clock — the sweeper must not touch
    it. Otherwise human-authored memos (created_at days ago) get
    falsely failed the moment someone sets up a sweeper."""
    room, _, _, user = await _seed_room_with_agent(db)
    now = _utcnow()
    task = Task(
        room_id=room.id,
        title="memo",
        status="todo",
        assignee_participant_id=None,
        assigned_at=None,
        created_by=user.id,
    )
    db.add(task)
    await db.commit()

    n = await sweep_stuck_tasks(db, manager=None, now=now)
    await db.refresh(task)
    assert n == 0
    assert task.status == "todo"


# ── Execution timeout ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_in_progress_past_execution_timeout_is_marked_failed(db):
    room, p, _, user = await _seed_room_with_agent(db)
    now = _utcnow()
    task = Task(
        room_id=room.id,
        title="long-runner",
        status="in_progress",
        assignee_participant_id=p.id,
        assigned_at=now - timedelta(hours=2),
        started_at=now - timedelta(seconds=TASK_EXECUTION_TIMEOUT_SECONDS + 60),
        created_by=user.id,
    )
    db.add(task)
    await db.commit()

    n = await sweep_stuck_tasks(db, manager=None, now=now)
    await db.commit()
    await db.refresh(task)
    assert n == 1
    assert task.status == "failed"
    assert task.error == "execution_timeout"


@pytest.mark.asyncio
async def test_in_progress_under_execution_threshold_is_left_alone(db):
    room, p, _, user = await _seed_room_with_agent(db)
    now = _utcnow()
    task = Task(
        room_id=room.id,
        title="working",
        status="in_progress",
        assignee_participant_id=p.id,
        assigned_at=now - timedelta(minutes=2),
        started_at=now - timedelta(seconds=TASK_EXECUTION_TIMEOUT_SECONDS - 60),
        created_by=user.id,
    )
    db.add(task)
    await db.commit()

    n = await sweep_stuck_tasks(db, manager=None, now=now)
    await db.refresh(task)
    assert n == 0
    assert task.status == "in_progress"


# ── Goal-derived bookkeeping ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_goal_derived_failure_increments_consecutive_failures(db):
    room, p, agent, user = await _seed_room_with_agent(db)
    goal = Goal(
        assignee_agent_id=agent.id,
        owner_id=user.id,
        report_room_id=room.id,
        title="t",
        spec="s",
        status="active",
        trigger_type="interval",
        trigger_config={"interval_seconds": 60},
        materialize="full",
        consecutive_failures=0,
    )
    db.add(goal)
    await db.flush()

    now = _utcnow()
    task = Task(
        room_id=room.id,
        title="t",
        status="todo",
        assignee_participant_id=p.id,
        assigned_at=now - timedelta(seconds=TASK_PICKUP_TIMEOUT_SECONDS + 30),
        created_by=user.id,
        goal_id=goal.id,
        triggered_by="scheduler",
    )
    db.add(task)
    await db.commit()

    await sweep_stuck_tasks(db, manager=None, now=now)
    await db.commit()
    await db.refresh(goal)
    assert goal.consecutive_failures == 1


@pytest.mark.asyncio
async def test_goal_pauses_on_threshold_failure(db):
    """Crossing the failure threshold flips the parent goal to
    ``paused`` so the scheduler stops re-firing the broken state."""
    room, p, agent, user = await _seed_room_with_agent(db)
    goal = Goal(
        assignee_agent_id=agent.id,
        owner_id=user.id,
        report_room_id=room.id,
        title="t",
        spec="s",
        status="active",
        trigger_type="interval",
        trigger_config={"interval_seconds": 60},
        materialize="full",
        # One short of the threshold — the upcoming sweep tips it over.
        consecutive_failures=GOAL_FAILURE_PAUSE_THRESHOLD - 1,
    )
    db.add(goal)
    await db.flush()

    now = _utcnow()
    task = Task(
        room_id=room.id,
        title="t",
        status="todo",
        assignee_participant_id=p.id,
        assigned_at=now - timedelta(seconds=TASK_PICKUP_TIMEOUT_SECONDS + 30),
        created_by=user.id,
        goal_id=goal.id,
        triggered_by="scheduler",
    )
    db.add(task)
    await db.commit()

    await sweep_stuck_tasks(db, manager=None, now=now)
    await db.commit()
    await db.refresh(goal)
    assert goal.consecutive_failures == GOAL_FAILURE_PAUSE_THRESHOLD
    assert goal.status == "paused"


@pytest.mark.asyncio
async def test_manual_task_failed_without_goal_bookkeeping(db):
    """``goal_id IS NULL`` rows have no goal counter to bump —
    ``apply_completion`` returns early. The status still flips so the
    UI no longer shows a ghost ``todo``."""
    room, p, _, user = await _seed_room_with_agent(db)
    now = _utcnow()
    task = Task(
        room_id=room.id,
        title="manual",
        status="todo",
        assignee_participant_id=p.id,
        assigned_at=now - timedelta(seconds=TASK_PICKUP_TIMEOUT_SECONDS + 30),
        created_by=user.id,
        goal_id=None,
        triggered_by="manual",
    )
    db.add(task)
    await db.commit()

    n = await sweep_stuck_tasks(db, manager=None, now=now)
    await db.commit()
    await db.refresh(task)
    assert n == 1
    assert task.status == "failed"


# ── Broadcast ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broadcast_emits_task_updated_frame_per_failure(db):
    """The right-rail subscribes to ``task.updated`` — without this
    fanout a swept failure would only show after a manual refresh."""
    room, p, _, user = await _seed_room_with_agent(db)
    now = _utcnow()
    task = Task(
        room_id=room.id,
        title="ping",
        status="todo",
        assignee_participant_id=p.id,
        assigned_at=now - timedelta(seconds=TASK_PICKUP_TIMEOUT_SECONDS + 30),
        created_by=user.id,
    )
    db.add(task)
    await db.commit()

    fake = _FakeManager()
    n = await sweep_stuck_tasks(db, manager=fake, now=now)  # type: ignore[arg-type]
    assert n == 1
    assert len(fake.calls) == 1
    room_id, frame = fake.calls[0]
    assert room_id == room.id
    # ``TaskUpdateOut`` carries an ``event`` discriminator + payload.
    payload = frame.task if hasattr(frame, "task") else frame.get("task")
    assert payload["status"] == "failed"
