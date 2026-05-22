"""Tests for ``GoalScheduler`` integration (#314).

Covers:
- ``_sweep`` runs ``sweep_stuck_tasks`` in its own session and
  commits, so a stuck task transitions even when no goals are due.
- ``manager`` propagates through ``_tick`` → ``trigger_goal`` →
  ``inject_task_assignment_message`` so scheduler-fired mentions are
  actually broadcast on the room channel.

We invoke the private ``_sweep`` / ``_tick`` methods directly rather
than spinning up the polling loop — timing-based tests on the loop
are flaky and the dispatch is what we want to verify here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

from anygarden.db.engine import build_session_factory
from anygarden.db.models import Agent, Goal, Participant, Room, Task, User
from anygarden.goals.policy import TASK_PICKUP_TIMEOUT_SECONDS
from anygarden.goals.scheduler import GoalScheduler


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def broadcast(self, room_id, frame, **_kwargs):
        self.calls.append((room_id, frame))

    async def push_to_users(self, user_ids, frame):
        pass


@pytest.mark.asyncio
async def test_sweep_marks_stuck_task_failed(engine):
    factory = build_session_factory(engine)
    async with factory() as db:
        user = User(email="u@x", password_hash="x", is_admin=True)
        agent = Agent(name="bot", engine="codex")
        room = Room(name="r")
        db.add_all([user, agent, room])
        await db.flush()
        p = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add(p)
        await db.flush()

        old = _utcnow() - timedelta(seconds=TASK_PICKUP_TIMEOUT_SECONDS + 30)
        task = Task(
            room_id=room.id,
            title="stuck",
            status="todo",
            assignee_participant_id=p.id,
            assigned_at=old,
            created_by=user.id,
        )
        db.add(task)
        await db.commit()
        task_id = task.id

    scheduler = GoalScheduler(factory)
    await scheduler._sweep()

    async with factory() as db:
        row = (
            await db.execute(select(Task).where(Task.id == task_id))
        ).scalar_one()
        assert row.status == "failed"
        assert row.error == "pickup_timeout"


@pytest.mark.asyncio
async def test_tick_passes_manager_through_to_inject(engine):
    """End-to-end: a due goal triggers a Task creation + an injected
    assignment message, and the manager held by the scheduler must
    receive a ``broadcast`` call so the agent's WS session actually
    wakes (this is the #314 bug fix). We assert at the lowest visible
    seam — ``_FakeManager.calls`` — instead of probing the helper
    path so the test stays robust to internal refactors."""
    factory = build_session_factory(engine)
    async with factory() as db:
        user = User(email="u@x", password_hash="x", is_admin=True)
        agent = Agent(name="bot", engine="codex")
        room = Room(name="r")
        db.add_all([user, agent, room])
        await db.flush()
        p = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add(p)
        await db.flush()
        # Goal with ``next_run_at`` in the past so ``_tick`` picks it up.
        goal = Goal(
            assignee_agent_id=agent.id,
            owner_id=user.id,
            report_room_id=room.id,
            title="ping",
            spec="say hi",
            status="active",
            trigger_type="interval",
            trigger_config={"interval_seconds": 60},
            materialize="full",
            next_run_at=_utcnow() - timedelta(seconds=5),
        )
        db.add(goal)
        await db.commit()

    fake = _FakeManager()
    scheduler = GoalScheduler(factory, manager=fake)  # type: ignore[arg-type]
    await scheduler._tick()

    # At least one broadcast — the synthetic task-assignment frame.
    # ``fanout_task_event`` does not run from the scheduler path
    # (it's invoked by the api/v1 router), so the only fanout we
    # expect is the message frame from ``inject_task_assignment_message``.
    assert any(
        room_id == room.id for room_id, _ in fake.calls
    ), f"expected a room.id={room.id} broadcast; got {fake.calls!r}"
