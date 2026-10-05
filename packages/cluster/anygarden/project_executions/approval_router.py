"""Operating-room reads and explicit human approval decisions."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from anygarden.auth.dependencies import Identity
from anygarden.db.execution_approval_models import ExecutionApproval
from anygarden.db.models import (
    Participant,
    ProjectExecution,
    Room,
    RoomArtifact,
    Task,
    TaskResult,
)
from anygarden.dependencies import get_current_identity, get_db
from anygarden.rooms.authorization import Capability, require_capability

router = APIRouter(tags=["project approvals"])


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]


def _targets(request: Request) -> dict:
    config = getattr(request.app.state, "config", None)
    return getattr(config, "project_action_targets", {})


async def _approval_to_out(
    db: AsyncSession,
    row: ExecutionApproval,
    *,
    targets: dict,
    can_decide: bool = False,
) -> dict:
    from anygarden.api.v1.tasks import _participant_display_name
    from anygarden.project_executions.approvals import (
        approval_is_current,
        approval_payload,
    )

    payload = approval_payload(row)
    participant = await db.get(Participant, row.requester_participant_id) if row.requester_participant_id else None
    if participant is not None and participant.room_id != row.task_room_id:
        participant = None
    payload["assignee_display_name"] = await _participant_display_name(db, participant)
    room = await db.get(Room, row.task_room_id)
    payload["task_room_name"] = room.name if room else None
    source_task = await db.get(Task, row.source_task_id)
    source_room = await db.get(Room, source_task.room_id) if source_task else None
    payload["source_task_title"] = source_task.title if source_task else None
    payload["source_task_room_id"] = source_room.id if source_room else None
    payload["source_task_room_name"] = source_room.name if source_room else None
    current = await approval_is_current(db, row, targets=targets)
    task = await db.get(Task, row.task_id)
    payload["is_current"] = current
    from anygarden.project_executions.authorization import disposition
    execution = await db.get(ProjectExecution, row.execution_id)
    payload["disposition"] = disposition(execution, row.input_revision)[1] if execution else "superseded"
    payload["permit_revoked"] = not current and row.status in {"pending", "approved", "rejected"}
    payload["can_decide"] = can_decide and current and row.status == "pending" and task is not None and task.status == "blocked"
    return payload


@router.get("/api/v1/rooms/{room_id}/execution-approvals")
async def list_execution_approvals(
    room_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    access = await require_capability(
        db, room_id=room_id, identity=identity, capability=Capability.TASK_READ
    )
    if access.room.project_id is None:
        return []
    source_task = aliased(Task)
    source_room = aliased(Room)
    artifact_room = aliased(Room)
    rows = (await db.execute(
        select(ExecutionApproval)
        .join(ProjectExecution, ProjectExecution.id == ExecutionApproval.execution_id)
        .join(Task, and_(Task.id == ExecutionApproval.task_id, Task.execution_id == ProjectExecution.id))
        .join(Room, Room.id == ExecutionApproval.task_room_id)
        .join(source_task, and_(source_task.id == ExecutionApproval.source_task_id, source_task.execution_id == ProjectExecution.id))
        .join(TaskResult, and_(TaskResult.id == ExecutionApproval.source_result_id, TaskResult.task_id == source_task.id, TaskResult.execution_id == ProjectExecution.id))
        .join(source_room, source_room.id == source_task.room_id)
        .join(RoomArtifact, and_(RoomArtifact.id == ExecutionApproval.artifact_id, RoomArtifact.room_id == ExecutionApproval.artifact_room_id))
        .join(artifact_room, artifact_room.id == RoomArtifact.room_id)
        .where(
            ExecutionApproval.operating_room_id == room_id,
            ProjectExecution.operating_room_id == room_id,
            ProjectExecution.project_id == access.room.project_id,
            Room.project_id == access.room.project_id,
            Task.room_id == ExecutionApproval.task_room_id,
            source_room.project_id == access.room.project_id,
            artifact_room.project_id == access.room.project_id,
        )
        .order_by(ExecutionApproval.created_at.desc(), ExecutionApproval.id)
    )).scalars().all()
    can_decide = identity.kind == "user" and not access.is_archived and (
        access.is_global_admin or access.effective_role in {"member", "admin", "owner"}
    )
    return [await _approval_to_out(db, row, targets=_targets(request), can_decide=can_decide) for row in rows]


@router.post("/api/v1/execution-approvals/{approval_id}/decision")
async def decide_execution_approval(
    approval_id: str,
    body: ApprovalDecision,
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from anygarden.project_executions.approvals import decide
    from anygarden.project_executions.service import ExecutionConflict

    try:
        row = await decide(
            db,
            useridentity=identity,
            approval_id=approval_id,
            decision=body.decision,
            targets=_targets(request),
        )
    except ExecutionConflict as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail}) from exc
    messages = list(db.info.pop("project_execution_messages", []))
    payload = await _approval_to_out(db, row, targets=_targets(request))
    await db.commit()
    from anygarden.mcp.project_tools import broadcast_project_messages
    from anygarden.messages.service import fanout_task_event

    await broadcast_project_messages(db, request=request, messages=messages)
    task = await db.get(Task, row.task_id)
    if task is not None:
        room = await db.get(Room, task.room_id)
        await fanout_task_event(
            db,
            manager=getattr(request.app.state, "connection_manager", None),
            event="updated",
            task=task,
            room_name=room.name if room else "",
        )
    return payload
