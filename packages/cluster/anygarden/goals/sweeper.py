"""Stuck-task sweeper for the Goal subsystem (#314).

Periodically scans the ``tasks`` table for rows that have been
assigned-but-not-started past ``TASK_PICKUP_TIMEOUT_SECONDS`` or
in-progress past ``TASK_EXECUTION_TIMEOUT_SECONDS``, and flips them to
``failed``. Goal-derived tasks are then run through ``apply_completion``
so the existing ``consecutive_failures`` / auto-pause policy fires.

Invoked from ``GoalScheduler._tick`` once per poll cycle. Runs in its
own short session so a sweep error can't poison goal triggering or
vice-versa.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Room, Task
from anygarden.goals.executor import apply_completion
from anygarden.goals.policy import (
    TASK_EXECUTION_TIMEOUT_SECONDS,
    TASK_PICKUP_TIMEOUT_SECONDS,
)
from anygarden.messages.service import fanout_task_event

if TYPE_CHECKING:
    from anygarden.ws.manager import ConnectionManager

log = logging.getLogger(__name__)


async def sweep_stuck_tasks(
    db: AsyncSession,
    *,
    manager: "ConnectionManager | None",
    now: datetime,
) -> int:
    """Find tasks past their pickup / execution timeouts and mark
    them ``failed``.

    Pickup timeout — ``status='todo'`` AND ``assigned_at`` older than
    ``TASK_PICKUP_TIMEOUT_SECONDS``. Means the assignee never began
    work (likely the assignment frame was missed, or the agent is
    offline / unresponsive).

    Execution timeout — ``status='in_progress'`` AND ``started_at``
    older than ``TASK_EXECUTION_TIMEOUT_SECONDS``. Means the agent
    began work but hasn't reported done/failed/blocked, so it's
    presumed wedged.

    For each task transitioned:
    - sets ``status='failed'`` and ``error`` to a reason code
    - delegates to ``apply_completion(task, final_status='failed')``
      so goal-derived tasks update the failure counter and may flip
      the parent goal to ``paused``
    - emits a ``task.updated`` WS frame so the right-rail and
      agent-profile views reflect the change

    Caller commits the transaction. Returns the number of tasks
    transitioned (useful for logs / metrics).
    """
    pickup_threshold = now - timedelta(seconds=TASK_PICKUP_TIMEOUT_SECONDS)
    exec_threshold = now - timedelta(seconds=TASK_EXECUTION_TIMEOUT_SECONDS)

    # Pickup timeout: assignee attached but never started.
    pickup_stmt = select(Task).where(
        Task.status == "todo",
        Task.assigned_at.is_not(None),
        Task.assigned_at < pickup_threshold,
    )
    stuck_todo = (await db.execute(pickup_stmt)).scalars().all()

    # Execution timeout: started but never finished.
    exec_stmt = select(Task).where(
        Task.status == "in_progress",
        Task.started_at.is_not(None),
        Task.started_at < exec_threshold,
    )
    stuck_running = (await db.execute(exec_stmt)).scalars().all()

    transitioned = 0
    for task in (*stuck_todo, *stuck_running):
        # Reason mirrors ``error`` for downstream observability — the
        # frontend goal-detail view shows the most recent failure to
        # explain why a goal got auto-paused.
        reason = (
            "pickup_timeout" if task.status == "todo" else "execution_timeout"
        )
        task.status = "failed"
        task.error = reason
        # ``apply_completion`` is a no-op on manual (``goal_id IS
        # NULL``) tasks, so we can call it unconditionally — it gates
        # itself. For goal-derived tasks it bumps
        # ``consecutive_failures`` and may flip the goal to ``paused``
        # via the existing #302 policy.
        await apply_completion(db, task, final_status="failed")

        # Frontend right-rail and agent-profile 2차 view both subscribe
        # to ``task.updated``. Without this fanout the UI would still
        # show the old ``todo`` / ``in_progress`` state until manual
        # refresh.
        if manager is not None:
            room_name = (
                await db.execute(
                    select(Room.name).where(Room.id == task.room_id)
                )
            ).scalar_one_or_none()
            await fanout_task_event(
                db,
                manager=manager,
                event="updated",
                task=task,
                room_name=room_name or "",
            )
        transitioned += 1

    if transitioned:
        log.info(
            "sweep_stuck_tasks_transitioned",
            extra={"count": transitioned},
        )
    return transitioned
