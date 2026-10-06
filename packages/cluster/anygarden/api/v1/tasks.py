"""REST endpoints for per-room task management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.api.v1.errors import make_api_error
from anygarden.auth.dependencies import Identity
from anygarden.db.models import (
    Agent,
    Message,
    Participant,
    ProjectExecution,
    Room,
    Task,
    TaskBlocker,
    User,
)
from anygarden.dependencies import get_current_identity, get_db
from anygarden.messages.serialization import message_to_frame
from anygarden.messages.service import (
    fanout_task_event,
    inject_task_assignment_message,
)
from anygarden.rooms.authorization import Capability, require_capability
from anygarden.task_service import (
    TaskMutationConflict,
    admin_requeue_task_cas,
    claim_task_cas,
    source_thread_root_id,
    transition_task_status_cas,
    update_open_task_cas,
    update_task_title_cas,
)

# #471 — validate ``status`` against the single canonical vocabulary
# (the same set the MCP ``mark_task_status`` path enforces) so the REST
# surface can't persist an out-of-band status the rest of the system
# never expects. Sourced from the import-light module to avoid dragging
# the MCP router into this module's import graph.
from anygarden.tasks_status import TASK_STATUS_VALUES
from anygarden.ws.protocol import MessageOut

router = APIRouter(tags=["tasks"])


def _validate_task_status(value: Optional[str]) -> Optional[str]:
    """Reject any status outside :data:`TASK_STATUS_VALUES` with a 422.

    ``None`` is passed through so the optional ``TaskUpdate.status`` field
    stays a no-op on partial updates (title/assignee-only changes)."""
    if value is not None and value not in TASK_STATUS_VALUES:
        raise ValueError(
            f"status must be one of {sorted(TASK_STATUS_VALUES)}"
        )
    return value


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    status: str = "todo"
    assignee_participant_id: Optional[str] = None
    spec: str | None = Field(default=None, max_length=100000)

    _check_status = field_validator("status")(_validate_task_status)


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    status: Optional[str] = None
    assignee_participant_id: Optional[str] = None

    _check_status = field_validator("status")(_validate_task_status)


class TaskOut(BaseModel):
    id: str
    room_id: str
    title: str
    status: str
    assignee_participant_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str
    source_message_id: Optional[str] = None
    source_thread_root_id: Optional[str] = None
    # #302 — goal-derived task fields. NULL on manual rows. Surfaced
    # so the frontend can render an "⚙ from <Goal title>" attribution
    # chip without a second round-trip.
    goal_id: Optional[str] = None
    triggered_by: str = "manual"
    is_interesting: bool = False
    spec: str | None = None
    result_markdown: str | None = None
    error: str | None = None
    dependency_results: list[dict] | None = None
    schedule_context: dict | None = None
    is_silent: bool = False
    execution_id: str | None = None
    parent_task_id: str | None = None
    parent_task_title: str | None = None
    input_revision: int | None = None
    delegation_depth: int = 0
    role: str | None = None
    result_version: int = 0
    room_name: str | None = None
    assignee_display_name: str | None = None
    execution_operating_room_id: str | None = None
    execution_source_message_id: str | None = None
    execution_objective: str | None = None
    execution_status: str | None = None
    execution_input_revision: int | None = None
    execution_operation_action: str | None = None
    execution_error: str | None = None
    is_current: bool = True
    disposition: str = "current"
    recovery: dict | None = None
    blocked_by: list[dict] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class MessageTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    assignee_participant_id: Optional[str] = None
    spec: str | None = Field(default=None, max_length=100000)


class TaskRequeue(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    assignee_participant_id: Optional[str] = None


async def _participant_display_name(db: AsyncSession, participant: Participant | None) -> str | None:
    if participant is None:
        return None
    if participant.agent_id:
        agent = await db.get(Agent, participant.agent_id)
        return agent.name if agent else None
    if participant.user_id:
        user = await db.get(User, participant.user_id)
        if user:
            return user.display_name or (user.email.split("@")[0] if user.email else "Guest")
    return None


async def _to_out(
    db: AsyncSession,
    task: Task,
    *,
    room: Room | None = None,
    assignee: Participant | None = None,
    execution: ProjectExecution | None = None,
    access=None,
) -> TaskOut:
    room = room or await db.get(Room, task.room_id)
    if assignee is None and task.assignee_participant_id:
        assignee = await db.get(Participant, task.assignee_participant_id)
    if assignee is not None and assignee.room_id != task.room_id:
        assignee = None
    if execution is None and task.execution_id:
        execution = await db.get(ProjectExecution, task.execution_id)
    if execution is not None and (room is None or room.project_id != execution.project_id):
        execution = None
    parent_title = None
    if task.parent_task_id and execution is not None:
        parent_title = await db.scalar(
            select(Task.title).join(Room, Room.id == Task.room_id).where(
                Task.id == task.parent_task_id,
                Task.execution_id == execution.id,
                Room.project_id == execution.project_id,
            )
        )
    blocked_by = []
    if task.status == "blocked":
        blockers = (
            select(Task.id, Task.title, Task.room_id, Task.status, Room.name)
            .join(TaskBlocker, TaskBlocker.blocked_by_task_id == Task.id)
            .join(Room, Room.id == Task.room_id)
            .where(TaskBlocker.task_id == task.id)
        )
        if execution is not None:
            blockers = blockers.where(
                Task.execution_id == execution.id,
                Room.project_id == execution.project_id,
            )
        else:
            blockers = blockers.where(Task.room_id == task.room_id)
        blocked_by = [
            {"task_id": blocker_id, "title": title, "room_id": blocker_room, "status": status, "room_name": room_name}
            for blocker_id, title, blocker_room, status, room_name in (await db.execute(blockers.order_by(Task.created_at, Task.id))).all()
        ]
    from anygarden.project_executions.authorization import disposition

    is_current, task_disposition = disposition(execution, task.input_revision) if execution else (True, "current")
    from anygarden.project_executions.recovery import (
        public_task_error,
        task_recovery_payload,
    )

    recovery = await task_recovery_payload(db, task, execution=execution, access=access) if execution else None
    from anygarden.project_executions.serialization import (
        execution_operation_projection,
    )

    operation = await execution_operation_projection(db, execution) if execution else {}
    return TaskOut(
        id=task.id,
        room_id=task.room_id,
        title=task.title,
        status=task.status,
        assignee_participant_id=task.assignee_participant_id,
        created_by=task.created_by,
        created_at=task.created_at.isoformat(),
        source_message_id=task.source_message_id,
        source_thread_root_id=await source_thread_root_id(db, task),
        goal_id=task.goal_id,
        triggered_by=task.triggered_by,
        is_interesting=task.is_interesting,
        spec=task.spec,
        result_markdown=task.result_markdown,
        error=public_task_error(task.status, task.error) if execution else task.error,
        dependency_results=task.dependency_results,
        schedule_context=task.schedule_context,
        is_silent=task.is_silent,
        execution_id=task.execution_id,
        parent_task_id=task.parent_task_id,
        parent_task_title=parent_title,
        input_revision=task.input_revision,
        delegation_depth=task.delegation_depth,
        role=task.role,
        result_version=task.result_version,
        room_name=room.name if room else None,
        assignee_display_name=await _participant_display_name(db, assignee),
        execution_operating_room_id=execution.operating_room_id if execution else None,
        execution_source_message_id=execution.source_message_id if execution else None,
        execution_objective=execution.objective if execution else None,
        execution_status=execution.status if execution else None,
        execution_input_revision=execution.input_revision if execution else None,
        is_current=is_current,
        disposition=task_disposition,
        recovery=recovery,
        blocked_by=blocked_by,
        **operation,
    )


def _reject_execution_mutation(task: Task) -> None:
    if task.execution_id is not None:
        raise make_api_error(
            status_code=409,
            code="PROJECT_EXECUTION_TASK_MANAGED",
            message="Execution tasks must use the project execution workflow",
        )


def _raise_conflict(exc: TaskMutationConflict) -> None:
    raise HTTPException(status_code=409, detail=exc.api_detail()) from exc


async def _raise_fresh_claim_conflict(
    db: AsyncSession,
    *,
    task_id: str,
    exc: TaskMutationConflict,
) -> None:
    """Report the committed winner, not the loser's stale read snapshot.

    SQLite can retain the transaction snapshot established by the pre-CAS
    task lookup even after a competing writer commits.  The failed UPDATE
    changed nothing, so end that request transaction before reading the row
    used in the 409 payload.
    """

    await db.rollback()
    current = await db.scalar(select(Task).where(Task.id == task_id))
    detail = exc.api_detail()
    detail["current_status"] = current.status if current else None
    detail["current_assignee_participant_id"] = (
        current.assignee_participant_id if current else None
    )
    raise HTTPException(status_code=409, detail=detail) from exc


def _is_system_source(message: Message) -> bool:
    metadata = message.extra_metadata or {}
    if metadata.get("system_origin") is not None:
        return True
    denied_keys = {
        "task_assignment",
        "room_query",
        "room_query_result",
        "room_query_forward",
        "routing_request_id",
    }
    return any(key in metadata for key in denied_keys)


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
        raise make_api_error(
            status_code=400,
            code="TASK_ASSIGNEE_NOT_IN_ROOM",
            message="assignee_participant_id is not a participant of this room",
            field="assignee_participant_id",
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
    return message_to_frame(msg)


@router.post("/api/v1/rooms/{room_id}/tasks", status_code=201, response_model=TaskOut)
async def create_task(
    room_id: str,
    body: TaskCreate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Create a task in a room."""
    await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.TASK_CREATE,
    )
    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if room is None:
        raise make_api_error(
            status_code=404,
            code="TASK_ROOM_NOT_FOUND",
            message="Room not found",
        )

    assignee: Optional[Participant] = None
    if body.assignee_participant_id is not None:
        await require_capability(
            db,
            room_id=room_id,
            identity=identity,
            capability=Capability.TASK_MANAGE,
        )
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
        spec=body.spec,
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

    return await _to_out(db, task)


@router.post(
    "/api/v1/rooms/{room_id}/messages/{message_id}/task",
    status_code=201,
    response_model=TaskOut,
)
async def create_message_task(
    room_id: str,
    message_id: str,
    body: MessageTaskCreate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Convert one same-room user message into a uniquely linked task."""

    access = await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.TASK_CREATE,
    )
    source = await db.scalar(
        select(Message).where(Message.id == message_id, Message.room_id == room_id)
    )
    if source is None:
        raise make_api_error(
            status_code=404,
            code="TASK_SOURCE_MESSAGE_NOT_FOUND",
            message="Message not found",
        )
    if _is_system_source(source):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "TASK_SYSTEM_SOURCE_FORBIDDEN",
                "detail": "System-generated messages cannot become tasks",
            },
        )

    assignee: Participant | None = None
    if body.assignee_participant_id is not None:
        await require_capability(
            db,
            room_id=room_id,
            identity=identity,
            capability=Capability.TASK_MANAGE,
        )
        assignee = await _validate_assignee_in_room(
            db, room_id, body.assignee_participant_id
        )

    task = Task(
        room_id=room_id,
        source_message_id=source.id,
        spec=body.spec if body.spec is not None else source.content,
        title=body.title,
        status="todo",
        assignee_participant_id=body.assignee_participant_id,
        assigned_at=(
            datetime.now(timezone.utc) if body.assignee_participant_id else None
        ),
        created_by=identity.id if identity.kind == "user" else None,
    )
    db.add(task)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        existing_id = await db.scalar(
            select(Task.id).where(Task.source_message_id == message_id)
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TASK_SOURCE_ALREADY_LINKED",
                "existing_task_id": existing_id,
            },
        ) from exc

    injected = await _maybe_inject(
        db,
        room=access.room,
        task=task,
        assignee=assignee,
        identity=identity,
        event="assigned",
    )
    await db.commit()
    await db.refresh(task)

    manager = _connection_manager(request)
    if injected is not None and manager is not None:
        await db.refresh(injected)
        await manager.broadcast(room_id, _message_to_frame(injected))
    await fanout_task_event(
        db,
        manager=manager,
        event="created",
        task=task,
        room_name=access.room.name,
    )
    return await _to_out(db, task)


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
    access = await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.TASK_READ,
    )
    stmt = (
        select(Task, Room, Participant, ProjectExecution)
        .join(Room, Room.id == Task.room_id)
        .outerjoin(Participant, Participant.id == Task.assignee_participant_id)
        .outerjoin(ProjectExecution, ProjectExecution.id == Task.execution_id)
        .where(
            or_(
                Task.room_id == room_id,
                and_(
                    ProjectExecution.operating_room_id == room_id,
                    ProjectExecution.project_id == access.room.project_id,
                    Room.project_id == access.room.project_id,
                    access.room.project_id is not None,
                ),
            )
        )
    )
    if status:
        stmt = stmt.where(Task.status == status)
    if goal_id:
        stmt = stmt.where(Task.goal_id == goal_id)
    else:
        stmt = stmt.where(Task.is_silent.is_(False))
    stmt = stmt.order_by(Task.created_at)
    rows = (await db.execute(stmt)).all()
    return [
        await _to_out(db, task, room=room, assignee=assignee, execution=execution, access=access)
        for task, room, assignee, execution in rows
    ]


@router.put("/api/v1/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: str,
    body: TaskUpdate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Apply a guarded edit or assignee-owned lifecycle transition."""
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise make_api_error(
            status_code=404,
            code="TASK_NOT_FOUND",
            message="Task not found",
        )

    access = await require_capability(
        db,
        room_id=task.room_id,
        identity=identity,
        capability=Capability.TASK_UPDATE,
        task=task,
        changed_fields=body.model_fields_set,
    )

    previous_assignee = task.assignee_participant_id
    _reject_execution_mutation(task)
    new_assignee_participant: Optional[Participant] = None
    previous_status = task.status

    assignee_was_set = "assignee_participant_id" in body.model_fields_set
    if assignee_was_set and body.status is not None:
        raise make_api_error(
            status_code=400,
            code="TASK_INVALID_MUTATION",
            message="Change an assignee or a status in one request, not both",
        )
    if assignee_was_set and body.assignee_participant_id is not None:
        new_assignee_participant = await _validate_assignee_in_room(
            db, task.room_id, body.assignee_participant_id
        )

    try:
        if assignee_was_set:
            if task.status != "todo":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "TASK_REQUEUE_REQUIRED",
                        "detail": (
                            "Running, blocked, and terminal tasks must use the "
                            "admin requeue endpoint before reassignment"
                        ),
                    },
                )
            task = await update_open_task_cas(
                db,
                task=task,
                title=body.title,
                set_assignee=True,
                assignee_participant_id=body.assignee_participant_id,
            )
        elif body.title is not None:
            task = await update_task_title_cas(db, task=task, title=body.title)

        if body.status is not None and body.status != previous_status:
            if body.status == "in_progress":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "TASK_CLAIM_REQUIRED",
                        "detail": "Use the atomic task claim endpoint",
                    },
                )
            if previous_status != "in_progress" or body.status not in {
                "blocked",
                "done",
                "failed",
            }:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "TASK_INVALID_TRANSITION",
                        "detail": f"Cannot transition {previous_status} to {body.status}",
                    },
                )
            is_admin = access.is_global_admin or access.effective_role in {
                "admin",
                "owner",
            }
            task = await transition_task_status_cas(
                db,
                task=task,
                target_status=body.status,
                participant_id=(
                    None
                    if is_admin
                    else access.participant.id if access.participant else None
                ),
            )
    except TaskMutationConflict as exc:
        _raise_conflict(exc)

    await db.flush()

    # #459 (Wave 2c) — resolve-wake. When this REST update flips the task
    # into a terminal status, run the same dependency-resolution hook the
    # MCP ``mark_task_status`` path uses so tasks blocked *by* this one get
    # returned to ``todo`` + re-woken once all prerequisites succeed.
    woken_blocker_ids: list[str] = []
    if (
        body.status is not None
        and body.status in ("done", "failed")
        and previous_status != body.status
    ):
        from anygarden.mcp.tools import resolve_task_blockers

        woken_blocker_ids = await resolve_task_blockers(
            db, completed_task_id=task.id
        )

    # #302 — materialize hook for goal-derived tasks. When the agent
    # marks a goal-derived task as ``done`` or ``failed``, run the
    # policy: increment/reset failure counter, optionally pause the
    # goal, and hide silent successes from ordinary work queues while
    # preserving the run ledger and its consumed idempotency key.
    task_was_hidden = False
    if (
        task.goal_id is not None
        and body.status is not None
        and body.status in ("done", "failed")
        and previous_status != body.status
    ):
        from anygarden.goals.executor import apply_completion

        task_was_hidden = await apply_completion(
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

    # #459 — snapshot the resolve-wake dependents (task + room + the
    # freshly-injected assignment mention) BEFORE commit so the WS fanout
    # can wake their assignees live, mirroring the MCP router path.
    woken_payloads: list[tuple[Task, Optional[Room], Optional[Message]]] = []
    for w_id in woken_blocker_ids:
        w_task = (
            await db.execute(select(Task).where(Task.id == w_id))
        ).scalar_one_or_none()
        if w_task is None:
            continue
        w_room = (
            await db.execute(select(Room).where(Room.id == w_task.room_id))
        ).scalar_one_or_none()
        w_msg = (
            await db.execute(
                select(Message)
                .where(Message.room_id == w_task.room_id)
                .order_by(Message.seq.desc())
                .limit(5)
            )
        ).scalars().all()
        w_match: Optional[Message] = None
        for m in w_msg:
            meta = m.extra_metadata or {}
            ta = meta.get("task_assignment")
            if ta and ta.get("task_id") == w_task.id:
                w_match = m
                break
        woken_payloads.append((w_task, w_room, w_match))

    task_snapshot_for_response = await _to_out(db, task)

    await db.commit()

    manager = _connection_manager(request)
    if injected is not None and manager is not None:
        await db.refresh(injected)
        await manager.broadcast(task.room_id, _message_to_frame(injected))

    # #459 — broadcast each woken dependent: its mention frame (wakes the
    # assignee agent) + a task.updated frame (so task views show todo).
    for w_task, w_room, w_match in woken_payloads:
        if manager is not None and w_match is not None:
            await manager.broadcast(w_task.room_id, _message_to_frame(w_match))
        await fanout_task_event(
            db,
            manager=manager,
            event="updated",
            task=w_task,
            room_name=w_room.name if w_room else "",
        )

    await db.refresh(task)
    if room is not None:
        await fanout_task_event(
            db,
            manager=manager,
            event="deleted" if task_was_hidden else fanout_event,  # type: ignore[arg-type]
            task=task,
            room_name=room.name,
        )

    return task_snapshot_for_response or await _to_out(db, task)


@router.post("/api/v1/tasks/{task_id}/claim", response_model=TaskOut)
async def claim_task(
    task_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Atomically claim an unassigned or caller-reserved todo task."""

    task = await db.scalar(select(Task).where(Task.id == task_id))
    if task is None:
        raise make_api_error(
            status_code=404,
            code="TASK_NOT_FOUND",
            message="Task not found",
        )
    access = await require_capability(
        db,
        room_id=task.room_id,
        identity=identity,
        capability=Capability.TASK_CLAIM,
        task=task,
    )
    if access.participant is None:
        raise make_api_error(
            status_code=403,
            code="TASK_ROOM_PARTICIPANT_REQUIRED",
            message="Room participant required",
        )
    _reject_execution_mutation(task)
    if identity.kind == "user" and not access.room.allow_human_assignment:
        raise make_api_error(
            status_code=403,
            code="TASK_HUMAN_ASSIGNMENT_DISABLED",
            message="Human task assignment is disabled for this room",
        )
    try:
        claimed = await claim_task_cas(
            db,
            task_id=task.id,
            room_id=task.room_id,
            participant_id=access.participant.id,
        )
    except TaskMutationConflict as exc:
        await _raise_fresh_claim_conflict(db, task_id=task.id, exc=exc)

    await db.commit()
    await db.refresh(claimed)
    manager = _connection_manager(request)
    await fanout_task_event(
        db,
        manager=manager,
        event="claimed",
        task=claimed,
        room_name=access.room.name,
    )
    return await _to_out(db, claimed)


@router.post("/api/v1/tasks/{task_id}/requeue", response_model=TaskOut)
async def requeue_task(
    task_id: str,
    body: TaskRequeue,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Explicitly return work to todo and record the administrator's reason."""

    task = await db.scalar(select(Task).where(Task.id == task_id))
    if task is None:
        raise make_api_error(
            status_code=404,
            code="TASK_NOT_FOUND",
            message="Task not found",
        )
    access = await require_capability(
        db,
        room_id=task.room_id,
        identity=identity,
        capability=Capability.TASK_MANAGE,
        task=task,
    )
    assignee: Participant | None = None
    _reject_execution_mutation(task)
    if body.assignee_participant_id is not None:
        assignee = await _validate_assignee_in_room(
            db, task.room_id, body.assignee_participant_id
        )
    try:
        changed = await admin_requeue_task_cas(
            db,
            task=task,
            actor_user_id=identity.id,
            reason=body.reason,
            assignee_participant_id=body.assignee_participant_id,
        )
    except TaskMutationConflict as exc:
        _raise_conflict(exc)

    injected: Message | None = None
    if assignee is not None and assignee.agent_id is not None:
        injected = await _maybe_inject(
            db,
            room=access.room,
            task=changed,
            assignee=assignee,
            identity=identity,
            event="reassigned",
        )
    await db.commit()
    await db.refresh(changed)
    manager = _connection_manager(request)
    if injected is not None and manager is not None:
        await db.refresh(injected)
        await manager.broadcast(changed.room_id, _message_to_frame(injected))
    await fanout_task_event(
        db,
        manager=manager,
        event="reassigned" if assignee is not None else "updated",
        task=changed,
        room_name=access.room.name,
    )
    return await _to_out(db, changed)


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
        raise make_api_error(
            status_code=404,
            code="TASK_NOT_FOUND",
            message="Task not found",
        )

    await require_capability(
        db,
        room_id=task.room_id,
        identity=identity,
        capability=Capability.TASK_MANAGE,
        task=task,
        changed_fields={"delete"},
    )

    _reject_execution_mutation(task)
    if task.source_message_id is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TASK_SOURCE_LINKED_DELETE_FORBIDDEN",
                "detail": "A message-linked task cannot be deleted",
            },
        )

    # Snapshot the fields the WS frame needs, then drop the row. After
    # the delete the ORM object's attributes are detached, so we resolve
    # ``room_name`` and the payload eagerly.
    room = (
        await db.execute(select(Room).where(Room.id == task.room_id))
    ).scalar_one_or_none()
    room_name = room.name if room else ""
    snapshot = task

    if task.goal_id is not None:
        if task.status not in {"done", "failed"}:
            raise HTTPException(status_code=409, detail="An active goal run cannot be cleared")
        task.is_silent = True
    else:
        from anygarden.db.native_invocation_models import NativeInvocationAccounting

        if await db.scalar(select(NativeInvocationAccounting.id).where(
            NativeInvocationAccounting.task_id == task_id,
        ).limit(1)):
            raise HTTPException(status_code=409, detail={
                "code": "NATIVE_INVOCATION_HISTORY_DELETE_FORBIDDEN",
                "detail": "A task with recorded invocation history cannot be deleted",
            })
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
