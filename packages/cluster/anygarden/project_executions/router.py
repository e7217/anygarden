"""Authorized project execution ledger and immutable result reads."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.execution_request_models import ExecutionRequest
from anygarden.db.models import Participant, ProjectExecution, Room, Task
from anygarden.dependencies import get_current_identity, get_db
from anygarden.rooms.authorization import Capability, require_capability

router = APIRouter(tags=["project executions"])
log = logging.getLogger(__name__)


class ExecutionAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=100000)


class ExecutionRetryBody(BaseModel):
    model_config = {"extra": "forbid"}
    operation_id: str = Field(min_length=36, max_length=36)
    expected_input_revision: int = Field(ge=1, strict=True)
    expected_request_id: str = Field(min_length=36, max_length=36)
    expected_attempt: int = Field(ge=1, strict=True)


class ExecutionMutationBody(BaseModel):
    model_config = {"extra": "forbid"}
    operation_id: str = Field(min_length=36, max_length=36)
    expected_input_revision: int = Field(ge=1, strict=True)
    expected_state_revision: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=10000)


class RevisionFile(BaseModel):
    model_config = {"extra": "forbid"}
    file_id: str = Field(min_length=36, max_length=36)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExecutionRevisionBody(ExecutionMutationBody):
    objective: str = Field(min_length=1, max_length=100000)
    constraints: str = Field(max_length=100000)
    completion_criteria: list[str] = Field(max_length=100)
    input_files: list[RevisionFile] = Field(max_length=32)
    change_scope: Literal["all"]


def _summary(execution: ProjectExecution) -> dict:
    return {
        "id": execution.id,
        "project_id": execution.project_id,
        "operating_room_id": execution.operating_room_id,
        "lead_agent_id": execution.lead_agent_id,
        "source_message_id": execution.source_message_id,
        "root_task_id": execution.root_task_id,
        "objective": execution.objective,
        "status": execution.status,
        "input_revision": execution.input_revision,
        "plan_sealed": execution.plan_sealed,
        "completion_criteria": execution.completion_criteria,
        "required_task_ids": execution.required_task_ids,
        "requires_qa": execution.requires_qa,
        "limits": execution.limits,
        "error": execution.error,
        "final_report_message_id": execution.final_report_message_id,
        "created_at": execution.created_at.isoformat(),
        "updated_at": execution.updated_at.isoformat(),
        "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
    }


@router.get("/api/v1/rooms/{room_id}/executions")
async def list_executions(
    room_id: str,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    access = await require_capability(
        db, room_id=room_id, identity=identity, capability=Capability.TASK_READ
    )
    if access.room.project_id is None:
        return []
    executions = (
        await db.execute(
            select(ProjectExecution)
            .where(
                ProjectExecution.operating_room_id == room_id,
                ProjectExecution.project_id == access.room.project_id,
            )
            .order_by(ProjectExecution.created_at.desc(), ProjectExecution.id)
        )
    ).scalars().all()
    from anygarden.project_executions.serialization import execution_summary
    return [await execution_summary(db, execution, access=access) for execution in executions]


@router.get("/api/v1/executions/{execution_id}")
async def get_execution(
    execution_id: str,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    execution = await db.get(ProjectExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Project execution not found")
    access = await require_capability(
        db,
        room_id=execution.operating_room_id,
        identity=identity,
        capability=Capability.TASK_READ,
    )
    if access.room.project_id != execution.project_id:
        raise HTTPException(status_code=404, detail="Project execution not found")
    from anygarden.project_executions.service import get_execution_detail

    return await get_execution_detail(db, execution_id, access=access)


async def _mutate_execution(execution_id, body, request, identity, db, *, action):
    from anygarden.project_executions.mutations import (
        cancel_execution,
        mutation_payload,
        revise_execution,
    )
    from anygarden.project_executions.serialization import fanout_execution_update
    from anygarden.project_executions.service import (
        ExecutionConflict,
        get_execution_detail,
    )

    try:
        if action == "revise":
            execution, mutation = await revise_execution(db, identity=identity, execution_id=execution_id,
                body=body.model_dump(), room_files_dir=Path(request.app.state.config.room_files_dir))
        else:
            execution, mutation = await cancel_execution(db, identity=identity, execution_id=execution_id,
                body=body.model_dump())
    except ExecutionConflict as exc:
        raise HTTPException(409, detail={"code": exc.code, "detail": exc.detail}) from exc
    messages = list(db.info.pop("project_execution_messages", []))
    access = await require_capability(db, room_id=execution.operating_room_id,
        identity=identity, capability=Capability.TASK_READ)
    result = await get_execution_detail(db, execution.id, access=access)
    result["operation"] = mutation_payload(mutation)
    await db.commit()
    # Fanout failure does not turn a committed revision/cancel into a failed
    # mutation. Polling, reconnect and durable outboxes recover delivery.
    import asyncio

    from anygarden.mcp.project_tools import broadcast_project_messages
    try:
        await asyncio.wait_for(broadcast_project_messages(db, request=request, messages=messages), timeout=2)
    except Exception:  # noqa: BLE001 — canonical committed response survives fanout
        log.warning("execution_mutation_fanout_deferred", extra={"execution_id": execution.id})
    await fanout_execution_update(db, manager=getattr(request.app.state, "connection_manager", None), execution_id=execution.id)
    return result


@router.post("/api/v1/executions/{execution_id}/input-revisions")
async def revise_project_execution(
    execution_id: str, body: ExecutionRevisionBody, request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _mutate_execution(execution_id, body, request, identity, db, action="revise")


@router.post("/api/v1/executions/{execution_id}/cancel")
async def cancel_project_execution(
    execution_id: str, body: ExecutionMutationBody, request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await _mutate_execution(execution_id, body, request, identity, db, action="cancel")


@router.post("/api/v1/execution-tasks/{task_id}/retry")
async def retry_execution_task(
    task_id: str, body: ExecutionRetryBody, request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from anygarden.api.v1.tasks import _to_out
    from anygarden.project_executions.recovery import (
        fanout_recovery_updates,
        retry_task,
    )
    from anygarden.project_executions.service import ExecutionConflict

    try:
        task, operation = await retry_task(db, identity=identity, task_id=task_id, body=body.model_dump())
    except ExecutionConflict as exc:
        from anygarden.project_executions.limits import apply_queued_usage_denials

        denials = dict(db.info.pop("project_execution_usage_denials", {}))
        await db.rollback()
        await apply_queued_usage_denials(request.app, denials=denials)
        raise HTTPException(409, detail={"code": exc.code, "detail": exc.detail}) from exc
    execution = await db.get(ProjectExecution, task.execution_id)
    access = await require_capability(db, room_id=execution.operating_room_id,
        identity=identity, capability=Capability.TASK_READ)
    result = (await _to_out(db, task, execution=execution, access=access)).model_dump()
    result["retry_operation"] = operation
    task_ids = db.info.pop("project_execution_recovery_tasks", set())
    await db.commit()
    await fanout_recovery_updates(request.app.state.session_factory,
        getattr(request.app.state, "connection_manager", None), task_ids)
    return result


async def _request_to_out(db: AsyncSession, row: ExecutionRequest, *, can_answer: bool = False, is_current: bool = True) -> dict:
    from anygarden.api.v1.tasks import _participant_display_name
    from anygarden.project_executions.requests import request_payload

    payload = request_payload(row)
    participant = await db.get(Participant, row.requester_participant_id) if row.requester_participant_id else None
    if participant is not None and participant.room_id != row.task_room_id:
        participant = None
    payload["assignee_display_name"] = await _participant_display_name(db, participant)
    payload["execution_source_message_id"] = row.source_message_id
    payload["can_answer"] = can_answer and row.status == "pending"
    payload["is_current"] = is_current
    return payload


@router.get("/api/v1/rooms/{room_id}/execution-requests")
async def list_execution_requests(
    room_id: str,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    access = await require_capability(
        db, room_id=room_id, identity=identity, capability=Capability.TASK_READ
    )
    if access.room.project_id is None:
        return []
    rows = (await db.execute(
        select(ExecutionRequest, ProjectExecution, Task, Room)
        .join(ProjectExecution, ProjectExecution.id == ExecutionRequest.execution_id)
        .join(Task, and_(Task.id == ExecutionRequest.task_id, Task.execution_id == ProjectExecution.id))
        .join(Room, Room.id == ExecutionRequest.task_room_id)
        .where(
            ExecutionRequest.operating_room_id == room_id,
            ProjectExecution.operating_room_id == room_id,
            ProjectExecution.project_id == access.room.project_id,
            Room.project_id == access.room.project_id,
            Task.room_id == ExecutionRequest.task_room_id,
        )
        .order_by(ExecutionRequest.created_at.desc(), ExecutionRequest.id)
    )).all()
    can_answer = identity.kind == "user" and not access.is_archived and (
        access.is_global_admin or access.effective_role in {"member", "admin", "owner"}
    )
    from anygarden.project_executions.service import ACTIVE_EXECUTION_STATES

    payloads = []
    for row, execution, task, task_room in rows:
        is_current = (
            execution.status in ACTIVE_EXECUTION_STATES
            and execution.input_revision == row.input_revision
            and task.input_revision == row.input_revision
            and row.requester_participant_id is not None
            and task.assignee_participant_id == row.requester_participant_id
            and task_room.archived_at is None
            and (execution.deadline_at is None or execution.deadline_at > datetime.now(UTC))
        )
        payloads.append(await _request_to_out(
            db, row, can_answer=can_answer and is_current and task.status == "blocked", is_current=is_current,
        ))
        from anygarden.project_executions.authorization import disposition
        payloads[-1]["disposition"] = disposition(execution, row.input_revision)[1]
    return payloads


@router.post("/api/v1/execution-requests/{request_id}/answer")
async def answer_execution_request(
    request_id: str,
    body: ExecutionAnswer,
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from anygarden.project_executions.requests import answer
    from anygarden.project_executions.service import ExecutionConflict

    try:
        row = await answer(db, useridentity=identity, request_id=request_id, answer=body.answer)
    except ExecutionConflict as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail}) from exc
    messages = list(db.info.pop("project_execution_messages", []))
    payload = await _request_to_out(db, row)
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
