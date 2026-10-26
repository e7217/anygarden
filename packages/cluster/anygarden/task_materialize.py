"""Create an assigned Task and wake its assignee (#930).

Shared by the Goal executor and the Campaign scheduler: both turn a
server-side trigger into an ordinary room Task assigned to one agent
participant, then inject the synthetic ``[TASK]`` mention that wakes the
agent through the normal mention path (#266). Keeping one funnel means a
scheduled goal run and a campaign cycle are dispatched exactly like any
other assignment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Participant, Room, Task
from anygarden.messages.service import inject_task_assignment_message

if TYPE_CHECKING:
    from anygarden.ws.manager import ConnectionManager


async def materialize_assigned_task(
    db: AsyncSession,
    *,
    room: Room,
    participant: Participant,
    title: str,
    spec: str | None,
    created_by: str | None,
    triggered_by: str,
    idempotency_key: str | None = None,
    manager: ConnectionManager | None = None,
    now: datetime | None = None,
    **fields: Any,
) -> Task:
    """Insert a ``todo`` Task assigned to *participant* and inject its
    assignment message. Caller validates the room/assignee and commits.

    ``fields`` are extra Task columns (``goal_id``, ``campaign_id``,
    ``schedule_context``, ``started_at`` …). ``idempotency_key`` is backed
    by the ``uq_tasks_idempotency_key`` UNIQUE index, so a duplicate
    trigger raises ``IntegrityError`` instead of creating a second Task.
    """
    now = now or datetime.now(UTC)
    task = Task(
        room_id=room.id,
        title=title,
        status="todo",
        assignee_participant_id=participant.id,
        assigned_at=now,  # #314 — the sweeper's pickup-timeout clock starts here
        created_by=created_by,
        triggered_by=triggered_by,
        spec=spec,
        idempotency_key=idempotency_key,
        **fields,
    )
    db.add(task)
    await db.flush()  # populate task.id before the message inject

    # Reuse #266 auto-execution: the synthetic mention wakes the agent.
    # ``manager`` broadcasts the frame on the room channel — without it the
    # mention sits in the DB and an always-on agent never wakes (#314).
    await inject_task_assignment_message(
        db,
        room=room,
        task=task,
        sender_participant_id=None,  # system-origin
        event="assigned",
        manager=manager,
    )
    return task


__all__ = ["materialize_assigned_task"]
