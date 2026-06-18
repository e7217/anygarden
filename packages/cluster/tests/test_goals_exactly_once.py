"""Exactly-once goal firing — CAS claim + idempotency + stampede caps
(#449, Wave 1b).

Covers the new firing contract:
- The same due slot fired twice produces exactly one Task
  (``Task.idempotency_key`` UNIQUE + CAS claim).
- A scheduler fire advances ``next_run_at`` via the CAS claim; a
  Run-now (``trigger_goal(trigger_source='manual')``) does NOT advance
  it (the latent Run-now-pushes-schedule bug is fixed).
- In-flight dedup — a goal with an open (``todo``/``in_progress``)
  Task is not re-fired on the next tick.
- Per-tick cap — more than ``MAX_GOALS_PER_TICK`` due goals fire
  ``MAX_GOALS_PER_TICK`` this tick, the rest on the next.

We drive the private ``_tick`` / ``trigger_goal`` directly; the
polling loop's timing is exercised elsewhere.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import func, select

from anygarden.db.engine import build_session_factory
from anygarden.db.models import Agent, Goal, Participant, Room, Task, User
from anygarden.goals.executor import trigger_goal
from anygarden.goals.scheduler import MAX_GOALS_PER_TICK, GoalScheduler


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def broadcast(self, room_id, frame, **_kwargs):
        self.calls.append((room_id, frame))

    async def push_to_users(self, user_ids, frame):
        pass


async def _seed_room_with_agent(
    db, *, owner_email: str = "u@x"
) -> tuple[Room, Participant, Agent, User]:
    user = User(email=owner_email, password_hash="x", is_admin=True)
    agent = Agent(name="bot", engine="codex")
    room = Room(name="r")
    db.add_all([user, agent, room])
    await db.flush()
    p = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add(p)
    await db.flush()
    return room, p, agent, user


def _make_goal(
    *, agent: Agent, user: User, room: Room, next_run_at: datetime | None
) -> Goal:
    return Goal(
        assignee_agent_id=agent.id,
        owner_id=user.id,
        report_room_id=room.id,
        title="ping",
        spec="say hi",
        status="active",
        trigger_type="interval",
        trigger_config={"interval_seconds": 60},
        materialize="full",
        next_run_at=next_run_at,
    )


# ── CAS / idempotency ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_slot_fired_twice_creates_exactly_one_task(engine):
    """Two ``_tick`` passes over the same due slot must dedup to one
    Task. The first claims+advances; the second sees ``next_run_at``
    already in the future (CAS guard misses) → no second fire."""
    factory = build_session_factory(engine)
    async with factory() as db:
        room, _, agent, user = await _seed_room_with_agent(db)
        goal = _make_goal(
            agent=agent, user=user, room=room,
            next_run_at=_utcnow() - timedelta(seconds=5),
        )
        db.add(goal)
        await db.commit()
        goal_id = goal.id

    scheduler = GoalScheduler(factory)
    await scheduler._tick()
    await scheduler._tick()

    async with factory() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Task)
                .where(Task.goal_id == goal_id)
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_scheduler_fire_advances_next_run_at(engine):
    """The CAS claim advances ``next_run_at`` to the next slot."""
    factory = build_session_factory(engine)
    async with factory() as db:
        room, _, agent, user = await _seed_room_with_agent(db)
        due = _utcnow() - timedelta(seconds=5)
        goal = _make_goal(agent=agent, user=user, room=room, next_run_at=due)
        db.add(goal)
        await db.commit()
        goal_id = goal.id

    scheduler = GoalScheduler(factory)
    await scheduler._tick()

    async with factory() as db:
        goal = await db.get(Goal, goal_id)
        # Advanced into the future (interval=60s from "now"), strictly
        # after the original due slot.
        assert goal.next_run_at is not None
        assert goal.next_run_at > due
        assert goal.next_run_at > _utcnow() - timedelta(seconds=5)
        assert goal.claimed_at is not None
        assert goal.last_run_at is not None


@pytest.mark.asyncio
async def test_run_now_does_not_advance_next_run_at(engine):
    """``trigger_goal`` (the Run-now path) must NOT push the schedule
    forward — the regression that the old ``trigger_type != 'manual'``
    advance introduced on cron/interval goals."""
    factory = build_session_factory(engine)
    async with factory() as db:
        room, _, agent, user = await _seed_room_with_agent(db)
        slot = _utcnow() + timedelta(hours=1)
        goal = _make_goal(agent=agent, user=user, room=room, next_run_at=slot)
        db.add(goal)
        await db.commit()
        goal_id = goal.id

        await trigger_goal(
            db, goal, trigger_source="manual", idempotency_key=f"{goal_id}:rn"
        )
        await db.commit()

    async with factory() as db:
        goal = await db.get(Goal, goal_id)
        # next_run_at unchanged; last_run_at recorded.
        assert goal.next_run_at == slot
        assert goal.last_run_at is not None


@pytest.mark.asyncio
async def test_trigger_goal_idempotency_key_collision_raises(engine):
    """Two fires with the same idempotency key collide on the UNIQUE
    index — the second raises IntegrityError (the seam the Run-now /
    scheduler paths rely on for dedup)."""
    from sqlalchemy.exc import IntegrityError

    factory = build_session_factory(engine)
    async with factory() as db:
        room, _, agent, user = await _seed_room_with_agent(db)
        goal = _make_goal(
            agent=agent, user=user, room=room, next_run_at=_utcnow()
        )
        db.add(goal)
        await db.commit()
        goal_id = goal.id

        await trigger_goal(
            db, goal, idempotency_key=f"{goal_id}:dup"
        )
        await db.commit()

        with pytest.raises(IntegrityError):
            await trigger_goal(
                db, goal, idempotency_key=f"{goal_id}:dup"
            )
            await db.commit()
        await db.rollback()


# ── In-flight dedup ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_in_flight_task_blocks_refire(engine):
    """A goal whose previous run is still ``todo`` must not be
    re-fired even when ``next_run_at`` is due again."""
    factory = build_session_factory(engine)
    async with factory() as db:
        room, p, agent, user = await _seed_room_with_agent(db)
        goal = _make_goal(
            agent=agent, user=user, room=room,
            next_run_at=_utcnow() - timedelta(seconds=5),
        )
        db.add(goal)
        await db.flush()
        # An open prior run.
        open_task = Task(
            room_id=room.id,
            title="prev",
            status="in_progress",
            assignee_participant_id=p.id,
            assigned_at=_utcnow(),
            created_by=user.id,
            goal_id=goal.id,
            triggered_by="scheduler",
        )
        db.add(open_task)
        await db.commit()
        goal_id = goal.id

    scheduler = GoalScheduler(factory)
    await scheduler._tick()

    async with factory() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Task)
                .where(Task.goal_id == goal_id)
            )
        ).scalar_one()
        # Still just the one open task — no sibling fire.
        assert count == 1
        goal = await db.get(Goal, goal_id)
        # And the schedule was NOT advanced (we skipped before the CAS).
        assert goal.next_run_at is not None
        assert goal.next_run_at <= _utcnow()


# ── Per-tick cap ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_per_tick_cap_limits_fires(engine):
    """More than ``MAX_GOALS_PER_TICK`` due goals → only the cap fires
    this tick; the remainder fire on the next tick (oldest-due first)."""
    factory = build_session_factory(engine)
    total = MAX_GOALS_PER_TICK + 5
    async with factory() as db:
        room, _, agent, user = await _seed_room_with_agent(db)
        base = _utcnow() - timedelta(seconds=600)
        for i in range(total):
            db.add(
                _make_goal(
                    agent=agent, user=user, room=room,
                    # Stagger the due times so ASC ordering is well-defined.
                    next_run_at=base + timedelta(seconds=i),
                )
            )
        await db.commit()

    scheduler = GoalScheduler(factory)
    await scheduler._tick()

    async with factory() as db:
        fired = (
            await db.execute(select(func.count()).select_from(Task))
        ).scalar_one()
        assert fired == MAX_GOALS_PER_TICK

    # Second tick fires the remaining backlog.
    await scheduler._tick()
    async with factory() as db:
        fired = (
            await db.execute(select(func.count()).select_from(Task))
        ).scalar_one()
        assert fired == total
