"""Goal Executor (#302 Phase 2).

The executor is the bridge between a scheduler trigger and the
existing task auto-execution flow. When a goal fires, the executor:

1. Resolves the goal's ``assignee_agent_id`` to a Participant in the
   ``report_room_id`` room. The Goal API guarantees this Participant
   exists at registration time, so we don't conjure one here.
2. Creates a new ``Task`` row carrying ``goal_id``, the spec snapshot,
   ``triggered_by='scheduler'``, and ``status='todo'``.
3. Calls ``inject_task_assignment_message`` (#266) which drops the
   synthetic mention into the room. The agent's existing
   ``decide_policy`` mention path picks it up — no new spawn pathway.
4. Updates ``Goal.last_run_at``. ``next_run_at`` is advanced by the
   scheduler's atomic CAS claim *before* it calls the executor (#449),
   not here — so the slot is consumed exactly once and a Run-now no
   longer pushes a scheduled goal's clock forward.

The materialize policy applies on completion, not at trigger time:
the Task is always created so the agent has a target to mark
``done`` / ``failed``. ``apply_completion`` (called from the existing
``PUT /api/v1/tasks/{id}`` handler) then hides a silent success from
ordinary task lists. Its durable row and consumed execution key remain
available in the goal's history.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Goal, Participant, Room, Task
from anygarden.goals.policy import (
    GOAL_FAILURE_PAUSE_THRESHOLD,
    MaterializeDecision,
    materialize_decision,
    trigger_timezone,
)
from anygarden.messages.service import inject_task_assignment_message
from anygarden.rooms.authorization import require_active_room

if TYPE_CHECKING:
    from anygarden.ws.manager import ConnectionManager

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GoalExecutionError(RuntimeError):
    """Raised when a goal cannot be fired — typically because the
    assignee agent is no longer a participant in the report room.
    The scheduler catches and paused the goal."""


async def find_assignee_participant(
    db: AsyncSession, *, room_id: str, agent_id: str
) -> Participant | None:
    """Resolve the goal's assignee agent to a Participant row in the
    report room. Returns ``None`` if the agent is not (or no longer)
    a member.

    The Goal API enforces membership at registration time so this
    only returns ``None`` if a room admin removed the agent after
    the goal was created — in that case the executor pauses the
    goal and posts a heads-up message.
    """
    stmt = select(Participant).where(
        Participant.room_id == room_id, Participant.agent_id == agent_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def trigger_goal(
    db: AsyncSession,
    goal: Goal,
    *,
    trigger_source: str = "scheduler",
    idempotency_key: str | None = None,
    scheduled_for: datetime | None = None,
    manager: ConnectionManager | None = None,
) -> Task:
    """Fire one execution of *goal*. Returns the freshly-created Task.

    Caller is responsible for ``await db.commit()`` — we keep it
    transactional so a failure to inject the mention rolls back the
    Task creation cleanly.

    ``idempotency_key`` (#449, Wave 1b) is stamped onto the Task. It
    is the deterministic per-slot token the UNIQUE index
    ``uq_tasks_idempotency_key`` enforces:
    - scheduler: ``f"{goal.id}:{int(slot.timestamp())}"`` where *slot*
      is the due ``next_run_at`` the CAS just claimed.
    - Run-now on a scheduled goal: the current ``next_run_at`` slot
      key, so a manual fire racing the scheduler dedups to one Task.
    - Run-now on a manual goal: ``f"{goal.id}:manual:{minute_bucket}"``.
    The callers compute it (the scheduler holds the pre-claim slot; the
    API holds the goal state) and pass it in; ``None`` leaves the key
    NULL for legacy callers / unit tests.

    The scheduler no longer advances ``next_run_at`` here — that is the
    CAS claim's job (#449). ``last_run_at`` is still set so the
    Run-now / manual path records the most recent fire without
    pushing the schedule forward.

    ``manager`` is forwarded to ``inject_task_assignment_message`` so
    the synthetic mention frame actually reaches the agent's WS
    session (#314). Defaults to ``None`` for legacy callers / unit
    tests that don't wire up a ``ConnectionManager``.

    ``scheduled_for`` is the claimed schedule slot, rather than the time
    execution happened. Manual fires leave it unset. Schedule context is
    snapshotted on the Task so later goal edits cannot rewrite its history.
    """
    if not goal.report_room_id:
        # Silent goals (no report room) aren't fireable in the MVP —
        # the agent has nowhere to read the mention from. The API
        # rejects such configurations at create time; this is a
        # belt-and-braces check for stale rows.
        raise GoalExecutionError(f"goal {goal.id} has no report_room_id — cannot fire")

    room = await db.get(Room, goal.report_room_id)
    if room is None:
        raise GoalExecutionError(
            f"room {goal.report_room_id} no longer exists — pausing goal {goal.id}"
        )
    try:
        require_active_room(room)
    except HTTPException as exc:
        raise GoalExecutionError(
            f"room {goal.report_room_id} is archived — pausing goal {goal.id}"
        ) from exc

    participant = await find_assignee_participant(
        db, room_id=goal.report_room_id, agent_id=goal.assignee_agent_id
    )
    if participant is None:
        raise GoalExecutionError(
            f"agent {goal.assignee_agent_id} is not a participant of "
            f"room {goal.report_room_id} — pausing goal {goal.id}"
        )

    now = _utcnow()
    task = Task(
        room_id=goal.report_room_id,
        title=goal.title,
        status="todo",
        assignee_participant_id=participant.id,
        assigned_at=now,  # #314 — sweeper pickup-timeout clock starts here
        created_by=goal.owner_id,
        # #302 — goal-derived fields
        goal_id=goal.id,
        triggered_by=trigger_source,
        spec=goal.spec,
        started_at=now,
        is_interesting=False,
        # #449 — deterministic dedup token; the UNIQUE index makes a
        # second fire of the same slot raise IntegrityError.
        idempotency_key=idempotency_key,
        schedule_context={
            "goal_id": goal.id,
            "scheduled_for": (
                scheduled_for.astimezone(UTC).isoformat()
                if scheduled_for is not None
                else None
            ),
            "timezone": trigger_timezone(goal.trigger_config).key,
            "overlap_policy": "wait",
            "trigger_source": trigger_source,
        },
    )
    db.add(task)
    await db.flush()  # populate task.id before message inject

    # Reuse #266 auto-execution: synthetic mention wakes the agent.
    # ``manager`` is forwarded so the helper also broadcasts the
    # ``MessageOut`` frame on the room channel — without this fanout
    # the mention sits silently in the DB and the agent never wakes
    # (#314). ``None`` is accepted for tests that don't wire up a
    # ConnectionManager.
    await inject_task_assignment_message(
        db,
        room=room,
        task=task,
        sender_participant_id=None,  # system-origin
        event="assigned",
        manager=manager,
    )

    # Update goal bookkeeping. ``last_run_at`` always tracks the most
    # recent fire. ``next_run_at`` is NO LONGER advanced here (#449):
    # the scheduler advances it atomically in the CAS claim *before*
    # calling this function, which (a) makes firing exactly-once under
    # concurrent ticks / replicas and (b) fixes the latent bug where a
    # Run-now on a cron/interval goal pushed the schedule forward
    # (the old advance keyed off ``trigger_type != "manual"``, not the
    # trigger source).
    goal.last_run_at = now

    log.info(
        "goal_fired",
        extra={
            "goal_id": goal.id,
            "task_id": task.id,
            "trigger": trigger_source,
            "agent": goal.assignee_agent_id,
            "room": goal.report_room_id,
        },
    )
    return task


async def apply_completion(
    db: AsyncSession,
    task: Task,
    *,
    final_status: str,
) -> bool:
    """Hook called from the Task PUT handler when a goal-derived task
    transitions to a terminal status.

    Returns ``True`` if the task is hidden from ordinary task lists
    (silent success on a materialize=interesting_only goal). The row,
    result, schedule provenance, and idempotency key remain durable.
    Caller commits the transaction. A persisted CAS marker makes repeat
    completion events harmless, including after server restart. Effects:
    - increments / resets ``Goal.consecutive_failures``
    - flips ``Goal.status='paused'`` if the threshold is crossed
    - records ``task.finished_at`` if it was not set by the transition
    """
    if task.goal_id is None:
        return False
    if final_status not in ("done", "failed"):
        return False

    goal = await db.get(Goal, task.goal_id)
    if goal is None:
        return False

    claimed = await db.scalar(
        update(Task)
        .where(
            Task.id == task.id,
            Task.goal_id == task.goal_id,
            Task.status == final_status,
            Task.goal_completion_applied.is_(False),
        )
        .values(
            goal_completion_applied=True,
            finished_at=task.finished_at or _utcnow(),
        )
        .returning(Task)
        .execution_options(populate_existing=True)
    )
    if claimed is None:
        current = await db.get(Task, task.id, populate_existing=True)
        return bool(current and current.is_silent)
    task = claimed

    # Failure counter — reset on success, increment on failure,
    # pause-flag once threshold crossed.
    old_status = goal.status
    new_count = 0 if final_status == "done" else Goal.consecutive_failures + 1
    pause_status = (
        Goal.status
        if final_status == "done"
        else case(
            (
                (Goal.status == "active") & (new_count >= GOAL_FAILURE_PAUSE_THRESHOLD),
                "paused",
            ),
            else_=Goal.status,
        )
    )
    goal = await db.scalar(
        update(Goal)
        .where(Goal.id == task.goal_id)
        .values(consecutive_failures=new_count, status=pause_status)
        .returning(Goal)
        .execution_options(populate_existing=True)
    )
    if goal is None:
        return False
    if old_status == "active" and goal.status == "paused":
        log.warning(
            "goal_paused_on_repeated_failure",
            extra={
                "goal_id": goal.id,
                "consecutive_failures": goal.consecutive_failures,
                "threshold": GOAL_FAILURE_PAUSE_THRESHOLD,
            },
        )

    # Materialize decision — silent success on interesting_only goals
    # hides the row so the rail does not accumulate "all green" noise.
    # Keeping the ledger preserves completion/retry history and the
    # consumed slot key; deleting it would allow a replay to execute again.
    decision = materialize_decision(
        materialize=goal.materialize,
        final_status=final_status,  # type: ignore[arg-type]
        is_interesting=task.is_interesting,
    )
    if decision is MaterializeDecision.DELETE:
        task.is_silent = True
        return True
    task.is_silent = False
    return False
