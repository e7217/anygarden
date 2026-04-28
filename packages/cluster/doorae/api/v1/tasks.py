"""REST endpoints for per-room task management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.db.models import Message, Participant, Room, Task
from doorae.dependencies import get_current_identity, get_db
from doorae.messages.service import (
    fanout_task_event,
    inject_task_assignment_message,
)
from doorae.ws.protocol import MessageOut

router = APIRouter(tags=["tasks"])


class TaskCreate(BaseModel):
    title: str
    status: str = "todo"
    assignee_participant_id: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    assignee_participant_id: Optional[str] = None


class TaskOut(BaseModel):
    id: str
    room_id: str
    title: str
    status: str
    assignee_participant_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str
    # #302 — goal-derived task fields. NULL on manual rows. Surfaced
    # so the frontend can render an "⚙ from <Goal title>" attribution
    # chip without a second round-trip.
    goal_id: Optional[str] = None
    triggered_by: str = "manual"
    is_interesting: bool = False

    model_config = {"from_attributes": True}


def _to_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id,
        room_id=task.room_id,
        title=task.title,
        status=task.status,
        assignee_participant_id=task.assignee_participant_id,
        created_by=task.created_by,
        created_at=task.created_at.isoformat(),
        goal_id=task.goal_id,
        triggered_by=task.triggered_by,
        is_interesting=task.is_interesting,
    )


async def _validate_assignee_in_room(
    db: AsyncSession, room_id: str, participant_id: str
) -> Participant:
    """Confirm *participant_id* is a participant of *room_id*. Raises 400
    when the participant is missing or belongs to a different room — the
    latter would silently break the mention path because the message
    fans out only to *room_id* subscribers (#266 plan §3.1)."""
    p = (
        await db.execute(select(Participant).where(Participant.id == participant_id))
    ).scalar_one_or_none()
    if p is None or p.room_id != room_id:
        raise HTTPException(
            status_code=400,
            detail="assignee_participant_id is not a participant of this room",
        )
    return p


async def _resolve_sender_participant_id(
    db: AsyncSession, room: Room, identity: Identity
) -> Optional[str]:
    """Pick the participant the synthetic message is recorded against.

    Order:
    1. Room orchestrator's participant (matches the natural "the
       conductor is dispatching" framing)
    2. The calling user's participant in this room
    3. ``None`` — system-origin marker is added in the helper

    See plan §3.2 decision 1 for the rationale.
    """
    if room.orchestrator_agent_id:
        orc_p = (
            await db.execute(
                select(Participant).where(
                    Participant.room_id == room.id,
                    Participant.agent_id == room.orchestrator_agent_id,
                )
            )
        ).scalar_one_or_none()
        if orc_p is not None:
            return orc_p.id

    if identity.kind == "user":
        caller_p = (
            await db.execute(
                select(Participant).where(
                    Participant.room_id == room.id,
                    Participant.user_id == identity.id,
                )
            )
        ).scalar_one_or_none()
        if caller_p is not None:
            return caller_p.id

    return None


async def _maybe_inject(
    db: AsyncSession,
    *,
    room: Room,
    task: Task,
    assignee: Optional[Participant],
    identity: Identity,
    event: str,
) -> Optional[Message]:
    """Drop a synthetic mention message iff the assignee is an agent.

    Returns the persisted :class:`Message` so the caller can broadcast
    a corresponding ``MessageOut`` frame on the room channel — the agent
    SDK's mention path is already wired to that frame, so no separate
    notification protocol is needed.

    Human assignees and ``None`` are no-ops by design (plan §3.5):
    auto-execution is reserved for agents.
    """
    if assignee is None or assignee.agent_id is None:
        return None
    sender_pid = await _resolve_sender_participant_id(db, room, identity)
    return await inject_task_assignment_message(
        db,
        room=room,
        task=task,
        sender_participant_id=sender_pid,
        event=event,  # type: ignore[arg-type]  # validated by callers
    )


def _connection_manager(request: Request):
    return getattr(request.app.state, "connection_manager", None)


def _message_to_frame(msg: Message) -> MessageOut:
    return MessageOut(
        id=msg.id,
        room_id=msg.room_id,
        participant_id=msg.participant_id,
        content=msg.content,
        seq=msg.seq,
        created_at=msg.created_at,
        metadata=msg.extra_metadata,
    )


@router.post("/api/v1/rooms/{room_id}/tasks", status_code=201, response_model=TaskOut)
async def create_task(
    room_id: str,
    body: TaskCreate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Create a task in a room."""
    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    assignee: Optional[Participant] = None
    if body.assignee_participant_id is not None:
        assignee = await _validate_assignee_in_room(
            db, room_id, body.assignee_participant_id
        )

    # #314 — start the sweeper's pickup-timeout clock the moment the
    # row gets an assignee. Stays NULL on unassigned tasks so the
    # sweeper's IS NOT NULL guard skips them.
    now = datetime.now(timezone.utc)
    task = Task(
        room_id=room_id,
        title=body.title,
        status=body.status,
        assignee_participant_id=body.assignee_participant_id,
        assigned_at=now if body.assignee_participant_id else None,
        created_by=identity.id if identity.kind == "user" else None,
    )
    db.add(task)
    await db.flush()  # surface ``task.id`` for the injection metadata

    injected = await _maybe_inject(
        db,
        room=room,
        task=task,
        assignee=assignee,
        identity=identity,
        event="assigned",
    )

    await db.commit()
    await db.refresh(task)

    manager = _connection_manager(request)
    if injected is not None and manager is not None:
        # Refresh in case the seq/created_at populated post-commit.
        await db.refresh(injected)
        await manager.broadcast(room_id, _message_to_frame(injected))
    await fanout_task_event(
        db, manager=manager, event="created", task=task, room_name=room.name
    )

    return _to_out(task)


@router.get("/api/v1/rooms/{room_id}/tasks", response_model=list[TaskOut])
async def list_tasks(
    room_id: str,
    status: Optional[str] = None,
    goal_id: Optional[str] = None,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """List tasks in a room, optionally filtered by status and/or
    ``goal_id`` (#302). The Goal detail's "recent runs" panel uses
    ``?goal_id=<id>`` to scope the room's tasks down to a single
    responsibility — backed by the ``ix_tasks_goal_created`` index."""
    stmt = select(Task).where(Task.room_id == room_id)
    if status:
        stmt = stmt.where(Task.status == status)
    if goal_id:
        stmt = stmt.where(Task.goal_id == goal_id)
    stmt = stmt.order_by(Task.created_at)
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_out(t) for t in rows]


@router.put("/api/v1/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: str,
    body: TaskUpdate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Update a task's title, status, or assignee.

    When *assignee_participant_id* changes to an agent participant, a
    synthetic mention message is injected so the agent's
    ``decide_policy`` wakes up via the existing mention path. Status-
    only or human-targeted changes are quiet.
    """
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    previous_assignee = task.assignee_participant_id
    new_assignee_participant: Optional[Participant] = None

    if body.assignee_participant_id is not None and (
        body.assignee_participant_id != previous_assignee
    ):
        new_assignee_participant = await _validate_assignee_in_room(
            db, task.room_id, body.assignee_participant_id
        )

    # Capture pre-state so the materialize hook (#302) can detect a
    # transition into a terminal status.
    previous_status = task.status

    if body.title is not None:
        task.title = body.title
    if body.status is not None:
        task.status = body.status
    if body.assignee_participant_id is not None:
        task.assignee_participant_id = body.assignee_participant_id
        # #314 — refresh the pickup-timeout clock on every (re)assignment
        # so the new assignee gets a fresh window.
        if body.assignee_participant_id != previous_assignee:
            task.assigned_at = datetime.now(timezone.utc)

    await db.flush()

    # #302 — materialize hook for goal-derived tasks. When the agent
    # marks a goal-derived task as ``done`` or ``failed``, run the
    # policy: increment/reset failure counter, optionally pause the
    # goal, and (for ``interesting_only`` silent successes) drop the
    # task row so the rail doesn't accumulate "all green" noise.
    task_was_deleted = False
    if (
        task.goal_id is not None
        and body.status is not None
        and body.status in ("done", "failed")
        and previous_status != body.status
    ):
        from doorae.goals.executor import apply_completion

        task_was_deleted = await apply_completion(
            db, task, final_status=body.status
        )

    injected: Optional[Message] = None
    room: Optional[Room] = None
    fanout_event = "updated"
    if new_assignee_participant is not None:
        room = (
            await db.execute(select(Room).where(Room.id == task.room_id))
        ).scalar_one_or_none()
        if room is not None:
            event = "reassigned" if previous_assignee else "assigned"
            fanout_event = event
            injected = await _maybe_inject(
                db,
                room=room,
                task=task,
                assignee=new_assignee_participant,
                identity=identity,
                event=event,
            )
    if room is None:
        room = (
            await db.execute(select(Room).where(Room.id == task.room_id))
        ).scalar_one_or_none()

    # Snapshot pre-commit so the WS frame can survive a delete on the
    # silent-success path. ``_to_out`` reads attributes that detach
    # after ``db.delete`` + ``commit`` — building the WS payload from
    # the snapshot keeps the response shape consistent.
    task_snapshot_for_response = _to_out(task) if not task_was_deleted else None

    await db.commit()

    manager = _connection_manager(request)
    if injected is not None and manager is not None:
        await db.refresh(injected)
        await manager.broadcast(task.room_id, _message_to_frame(injected))

    if task_was_deleted:
        # Treat the silent-success delete as a regular task delete on
        # the wire so subscribers prune the row from their local cache.
        if room is not None:
            await fanout_task_event(
                db,
                manager=manager,
                event="deleted",
                task=task,
                room_name=room.name,
            )
        # Mirror the legacy DELETE handler's response shape so the
        # client can detect the row vanished and update its UI.
        return TaskOut(
            id=task.id,
            room_id=task.room_id,
            title=task.title,
            status=body.status or task.status,
            assignee_participant_id=task.assignee_participant_id,
            created_by=task.created_by,
            created_at=task.created_at.isoformat(),
            goal_id=task.goal_id,
            triggered_by=task.triggered_by,
            is_interesting=task.is_interesting,
        )

    await db.refresh(task)
    if room is not None:
        await fanout_task_event(
            db,
            manager=manager,
            event=fanout_event,  # type: ignore[arg-type]
            task=task,
            room_name=room.name,
        )

    return task_snapshot_for_response or _to_out(task)


@router.delete("/api/v1/tasks/{task_id}", status_code=200)
async def delete_task(
    task_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Delete a task."""
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Snapshot the fields the WS frame needs, then drop the row. After
    # the delete the ORM object's attributes are detached, so we resolve
    # ``room_name`` and the payload eagerly.
    room = (
        await db.execute(select(Room).where(Room.id == task.room_id))
    ).scalar_one_or_none()
    room_name = room.name if room else ""
    snapshot = task

    await db.delete(task)
    await db.commit()

    manager = _connection_manager(request)
    await fanout_task_event(
        db,
        manager=manager,
        event="deleted",
        task=snapshot,
        room_name=room_name,
    )
    return {"deleted": True}
