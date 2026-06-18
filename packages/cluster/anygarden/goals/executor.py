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
``PUT /api/v1/tasks/{id}`` handler) then deletes the row if the goal
is ``interesting_only`` and the result was a silent success.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Goal, Participant, Room, Task
from anygarden.goals.policy import (
    GOAL_FAILURE_PAUSE_THRESHOLD,
    MaterializeDecision,
    apply_completion_to_failure_counter,
    materialize_decision,
)
from anygarden.messages.service import inject_task_assignment_message

if TYPE_CHECKING:
    from anygarden.ws.manager import ConnectionManager

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    manager: "ConnectionManager | None" = None,
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
    """
    if not goal.report_room_id:
        # Silent goals (no report room) aren't fireable in the MVP —
        # the agent has nowhere to read the mention from. The API
        # rejects such configurations at create time; this is a
        # belt-and-braces check for stale rows.
        raise GoalExecutionError(
            f"goal {goal.id} has no report_room_id — cannot fire"
        )

    participant = await find_assignee_participant(
        db, room_id=goal.report_room_id, agent_id=goal.assignee_agent_id
    )
    if participant is None:
        raise GoalExecutionError(
            f"agent {goal.assignee_agent_id} is not a participant of "
            f"room {goal.report_room_id} — pausing goal {goal.id}"
        )

    room = await db.get(Room, goal.report_room_id)
    if room is None:
        raise GoalExecutionError(
            f"room {goal.report_room_id} no longer exists — pausing "
            f"goal {goal.id}"
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

    Returns ``True`` if the task was deleted (silent success on a
    materialize=interesting_only goal). Caller commits the
    transaction. Side-effects:
    - increments / resets ``Goal.consecutive_failures``
    - flips ``Goal.status='paused'`` if the threshold is crossed
    - sets ``task.finished_at = now``
    """
    if task.goal_id is None:
        return False
    if final_status not in ("done", "failed"):
        return False

    goal = await db.get(Goal, task.goal_id)
    if goal is None:
        return False

    now = _utcnow()
    task.finished_at = now

    # Failure counter — reset on success, increment on failure,
    # pause-flag once threshold crossed.
    counter = apply_completion_to_failure_counter(
        current=goal.consecutive_failures, final_status=final_status  # type: ignore[arg-type]
    )
    goal.consecutive_failures = counter.new_count
    if counter.pause and goal.status == "active":
        goal.status = "paused"
        log.warning(
            "goal_paused_on_repeated_failure",
            extra={
                "goal_id": goal.id,
                "consecutive_failures": counter.new_count,
                "threshold": GOAL_FAILURE_PAUSE_THRESHOLD,
            },
        )

    # Materialize decision — silent success on interesting_only goals
    # removes the row so the rail does not accumulate "all green"
    # noise. Failures and full-mode rows always persist.
    decision = materialize_decision(
        materialize=goal.materialize,
        final_status=final_status,  # type: ignore[arg-type]
        is_interesting=task.is_interesting,
    )
    if decision is MaterializeDecision.DELETE:
        await db.delete(task)
        return True
    return False
