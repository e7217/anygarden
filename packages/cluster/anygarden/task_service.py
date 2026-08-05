"""Shared, compare-and-set task lifecycle operations.

REST, MCP, scheduler, and WebSocket callers use these primitives so a stale
read cannot overwrite a claim, removal, archive, or status transition.
Callers remain responsible for transport-specific authorization and fanout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Message, Participant, Room, RoomAuthorizationAudit, Task


@dataclass(frozen=True, slots=True)
class TaskMutationConflict(Exception):
    """A stable conflict raised after a CAS predicate loses."""

    code: str
    detail: str
    current_status: str | None = None
    current_assignee_participant_id: str | None = None

    def api_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "current_status": self.current_status,
            "current_assignee_participant_id": self.current_assignee_participant_id,
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _active_room_predicate(room_id: str):
    return exists(select(Room.id).where(Room.id == room_id, Room.archived_at.is_(None)))


def _current_assignee_predicate(participant_id: str | None):
    if participant_id is None:
        return Task.assignee_participant_id.is_(None)
    return Task.assignee_participant_id == participant_id


async def source_thread_root_id(db: AsyncSession, task: Task) -> str | None:
    """Return the canonical root for a source-linked task."""

    if task.source_message_id is None:
        return None
    source = await db.scalar(
        select(Message).where(
            Message.id == task.source_message_id,
            Message.room_id == task.room_id,
        )
    )
    if source is None:
        return None
    return source.root_message_id or source.id


async def _current_task(db: AsyncSession, task_id: str) -> Task | None:
    return await db.scalar(select(Task).where(Task.id == task_id))


async def claim_task_cas(
    db: AsyncSession,
    *,
    task_id: str,
    room_id: str,
    participant_id: str,
) -> Task:
    """Atomically claim an open task for the current participant.

    The successful path is one SQL UPDATE. The participant and active-room
    EXISTS predicates make removal/archive effective even after a stale REST,
    MCP, or WebSocket read. A reserved todo can only be accepted by its
    designated participant.
    """

    now = _utcnow()
    result = await db.execute(
        update(Task)
        .where(
            Task.id == task_id,
            Task.room_id == room_id,
            Task.status == "todo",
            or_(
                Task.assignee_participant_id.is_(None),
                Task.assignee_participant_id == participant_id,
            ),
            exists(
                select(Participant.id).where(
                    Participant.id == participant_id,
                    Participant.room_id == room_id,
                )
            ),
            _active_room_predicate(room_id),
        )
        .values(
            status="in_progress",
            assignee_participant_id=participant_id,
            assigned_at=now,
            started_at=now,
            finished_at=None,
            error=None,
        )
        .returning(Task)
    )
    claimed = result.scalar_one_or_none()
    if claimed is not None:
        return claimed

    current = await _current_task(db, task_id)
    raise TaskMutationConflict(
        code="TASK_CLAIM_CONFLICT",
        detail="Task is no longer claimable by this participant",
        current_status=current.status if current else None,
        current_assignee_participant_id=(
            current.assignee_participant_id if current else None
        ),
    )


async def transition_task_status_cas(
    db: AsyncSession,
    *,
    task: Task,
    target_status: str,
    participant_id: str | None = None,
) -> Task:
    """CAS an allowed status transition against the caller's observed row."""

    values: dict[str, Any] = {"status": target_status}
    now = _utcnow()
    if target_status == "in_progress":
        values["started_at"] = now
    if target_status == "todo":
        values["assigned_at"] = now if task.assignee_participant_id else None
        values["started_at"] = None
        values["finished_at"] = None
        values["error"] = None
    if target_status in {"done", "failed"}:
        values["finished_at"] = now

    predicates = [
        Task.id == task.id,
        Task.room_id == task.room_id,
        Task.status == task.status,
        _current_assignee_predicate(task.assignee_participant_id),
        _active_room_predicate(task.room_id),
    ]
    if participant_id is not None:
        predicates.extend(
            [
                Task.assignee_participant_id == participant_id,
                exists(
                    select(Participant.id).where(
                        Participant.id == participant_id,
                        Participant.room_id == task.room_id,
                    )
                ),
            ]
        )

    result = await db.execute(
        update(Task).where(*predicates).values(**values).returning(Task)
    )
    changed = result.scalar_one_or_none()
    if changed is not None:
        return changed
    current = await _current_task(db, task.id)
    raise TaskMutationConflict(
        code="TASK_UPDATE_CONFLICT",
        detail="Task changed before the status transition completed",
        current_status=current.status if current else None,
        current_assignee_participant_id=(
            current.assignee_participant_id if current else None
        ),
    )


async def update_open_task_cas(
    db: AsyncSession,
    *,
    task: Task,
    title: str | None,
    set_assignee: bool,
    assignee_participant_id: str | None,
) -> Task:
    """Edit a todo task or its reservation without racing a claim."""

    values: dict[str, Any] = {}
    if title is not None:
        values["title"] = title
    if set_assignee:
        values["assignee_participant_id"] = assignee_participant_id
        values["assigned_at"] = _utcnow() if assignee_participant_id else None
    if not values:
        return task

    predicates = [
        Task.id == task.id,
        Task.room_id == task.room_id,
        Task.status == "todo",
        _current_assignee_predicate(task.assignee_participant_id),
        _active_room_predicate(task.room_id),
    ]
    if set_assignee and assignee_participant_id is not None:
        predicates.append(
            exists(
                select(Participant.id).where(
                    Participant.id == assignee_participant_id,
                    Participant.room_id == task.room_id,
                )
            )
        )
    result = await db.execute(
        update(Task).where(*predicates).values(**values).returning(Task)
    )
    changed = result.scalar_one_or_none()
    if changed is not None:
        return changed
    current = await _current_task(db, task.id)
    raise TaskMutationConflict(
        code="TASK_UPDATE_CONFLICT",
        detail="Task changed before the update completed",
        current_status=current.status if current else None,
        current_assignee_participant_id=(
            current.assignee_participant_id if current else None
        ),
    )


async def update_task_title_cas(
    db: AsyncSession,
    *,
    task: Task,
    title: str,
) -> Task:
    """Edit a task title without overwriting a concurrent lifecycle change."""

    predicates = [
        Task.id == task.id,
        Task.room_id == task.room_id,
        Task.status == task.status,
        _current_assignee_predicate(task.assignee_participant_id),
        _active_room_predicate(task.room_id),
    ]
    result = await db.execute(
        update(Task).where(*predicates).values(title=title).returning(Task)
    )
    changed = result.scalar_one_or_none()
    if changed is not None:
        return changed
    current = await _current_task(db, task.id)
    raise TaskMutationConflict(
        code="TASK_UPDATE_CONFLICT",
        detail="Task changed before the title update completed",
        current_status=current.status if current else None,
        current_assignee_participant_id=(
            current.assignee_participant_id if current else None
        ),
    )


async def admin_requeue_task_cas(
    db: AsyncSession,
    *,
    task: Task,
    actor_user_id: str,
    reason: str,
    assignee_participant_id: str | None,
) -> Task:
    """Return any non-todo task to todo with a durable audit reason."""

    predicates = [
        Task.id == task.id,
        Task.room_id == task.room_id,
        Task.status == task.status,
        _current_assignee_predicate(task.assignee_participant_id),
        _active_room_predicate(task.room_id),
    ]
    if assignee_participant_id is not None:
        predicates.append(
            exists(
                select(Participant.id).where(
                    Participant.id == assignee_participant_id,
                    Participant.room_id == task.room_id,
                )
            )
        )
    result = await db.execute(
        update(Task)
        .where(*predicates)
        .values(
            status="todo",
            assignee_participant_id=assignee_participant_id,
            assigned_at=_utcnow() if assignee_participant_id else None,
            started_at=None,
            finished_at=None,
            error=None,
        )
        .returning(Task)
    )
    changed = result.scalar_one_or_none()
    if changed is None:
        current = await _current_task(db, task.id)
        raise TaskMutationConflict(
            code="TASK_REQUEUE_CONFLICT",
            detail="Task changed before the requeue completed",
            current_status=current.status if current else None,
            current_assignee_participant_id=(
                current.assignee_participant_id if current else None
            ),
        )

    db.add(
        RoomAuthorizationAudit(
            actor_user_id=actor_user_id,
            room_id=task.room_id,
            scope="task.requeue",
            capability="task.manage",
            outcome="requeued",
            details={
                "task_id": task.id,
                "reason": reason,
                "from_status": task.status,
                "from_assignee_participant_id": task.assignee_participant_id,
                "to_assignee_participant_id": assignee_participant_id,
            },
        )
    )
    await db.flush()
    return changed


async def release_participant_tasks(
    db: AsyncSession,
    *,
    room_id: str,
    participant_id: str,
) -> list[str]:
    """Release reservations/running work when a participant is removed."""

    result = await db.execute(
        update(Task)
        .where(
            Task.room_id == room_id,
            Task.assignee_participant_id == participant_id,
            Task.status.in_(("todo", "in_progress")),
        )
        .values(
            status="todo",
            assignee_participant_id=None,
            assigned_at=None,
            started_at=None,
            finished_at=None,
            error="assignee_removed",
        )
        .returning(Task.id)
    )
    return list(result.scalars().all())


async def redispatch_task_cas(
    db: AsyncSession,
    *,
    task: Task,
    reason: str,
) -> Task | None:
    """Requeue the still-current assignee after a failed assignment turn."""

    assignee_id = task.assignee_participant_id
    if assignee_id is None:
        return None
    result = await db.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.room_id == task.room_id,
            Task.status == task.status,
            Task.status.in_(("todo", "in_progress")),
            Task.assignee_participant_id == assignee_id,
            exists(
                select(Participant.id).where(
                    Participant.id == assignee_id,
                    Participant.room_id == task.room_id,
                )
            ),
            _active_room_predicate(task.room_id),
        )
        .values(
            status="todo",
            assigned_at=_utcnow(),
            started_at=None,
            finished_at=None,
            error=f"redispatch:{reason}",
        )
        .returning(Task)
    )
    return result.scalar_one_or_none()


async def fail_stale_task_cas(
    db: AsyncSession,
    *,
    task: Task,
    reason: str,
) -> Task | None:
    """Fail a still-current stale task without crossing an archive/CAS race."""

    result = await db.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.room_id == task.room_id,
            Task.status == task.status,
            Task.status.in_(("todo", "in_progress")),
            _current_assignee_predicate(task.assignee_participant_id),
            _active_room_predicate(task.room_id),
        )
        .values(status="failed", error=reason, finished_at=_utcnow())
        .returning(Task)
    )
    return result.scalar_one_or_none()


__all__ = [
    "TaskMutationConflict",
    "admin_requeue_task_cas",
    "claim_task_cas",
    "fail_stale_task_cas",
    "release_participant_tasks",
    "redispatch_task_cas",
    "source_thread_root_id",
    "transition_task_status_cas",
    "update_open_task_cas",
    "update_task_title_cas",
]
