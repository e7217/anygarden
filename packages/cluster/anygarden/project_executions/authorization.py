"""Shared immutable turn scope and execution-row authorization fence."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import exists, or_, select, update

from anygarden.db.models import (
    AgentTurn,
    ExecutionInputRevision,
    ExecutionStop,
    ProjectExecution,
    Room,
    Task,
)

ACTIVE_STATES = frozenset({"planning", "running", "waiting_children"})
SAFE_STOP_STATES = frozenset({"confirmed", "not_started", "already_finished"})
MAX_SNAPSHOT_WIRE_BYTES = 768 * 1024


def frozen_input_payload(execution: ProjectExecution, revision: ExecutionInputRevision) -> dict:
    payload = {
        "execution_id": execution.id,
        "input_revision": revision.revision,
        "objective": revision.objective,
        "constraints": revision.user_constraints,
        "completion_criteria": revision.completion_criteria,
        "input_files": revision.input_files,
    }
    if len(json.dumps(payload, ensure_ascii=True).encode("utf-8")) > MAX_SNAPSHOT_WIRE_BYTES:
        from anygarden.project_executions.service import ExecutionConflict
        raise ExecutionConflict("INPUT_SNAPSHOT_TOO_LARGE", "The immutable input snapshot exceeds the runtime transport size limit")
    return payload


async def bind_execution_turn(db, *, turn: AgentTurn, task: Task) -> None:
    """Bind once, including a root promoted after its native invocation started."""
    from anygarden.project_executions.service import ExecutionConflict

    if not task.execution_id or task.input_revision is None or turn.task_id != task.id:
        raise ExecutionConflict("TURN_EXECUTION_BINDING_INVALID", "An execution turn must bind its actual source-linked task")
    if turn.execution_id is not None or turn.execution_input_revision is not None:
        if turn.execution_id != task.execution_id or turn.execution_input_revision != task.input_revision:
            raise ExecutionConflict("TURN_EXECUTION_BINDING_IMMUTABLE", "A turn cannot move between execution input revisions")
        from anygarden.project_executions.usage import adopt_source_invocations

        execution = await db.get(ProjectExecution, task.execution_id)
        await adopt_source_invocations(db, execution=execution, turn=turn)
        return
    await db.flush()
    changed = await db.scalar(update(AgentTurn).where(
        AgentTurn.request_id == turn.request_id,
        AgentTurn.task_id == task.id,
        AgentTurn.execution_id.is_(None),
        AgentTurn.execution_input_revision.is_(None),
    ).values(execution_id=task.execution_id, execution_input_revision=task.input_revision).returning(AgentTurn.request_id))
    if changed is None:
        raise ExecutionConflict("TURN_EXECUTION_BINDING_CONFLICT", "Turn scope changed while binding")
    await db.refresh(turn)
    from anygarden.project_executions.usage import adopt_source_invocations

    execution = await db.get(ProjectExecution, task.execution_id)
    await adopt_source_invocations(db, execution=execution, turn=turn)


async def validate_execution_turn(db, turn: AgentTurn, *, lock: bool = False) -> tuple[bool, str | None, dict | None]:
    """Authorize a bound revision; lock serializes with user mutation CAS.

    Ordinary room conversation remains nullable. A task-linked execution can
    never fall back to ordinary conversation when its binding is missing.
    Caller retains the existing workspace, lease and participant gates.
    """
    task = await db.get(Task, turn.task_id, populate_existing=True) if turn.task_id else None
    if turn.execution_id is None and turn.execution_input_revision is None:
        if task is not None and task.execution_id is not None:
            return False, "TURN_EXECUTION_BINDING_MISSING", None
        return True, None, None
    if (task is None or task.execution_id != turn.execution_id
        or task.input_revision != turn.execution_input_revision
        or task.room_id != turn.room_id
        or task.assignee_participant_id != turn.target_participant_id):
        return False, "TURN_EXECUTION_BINDING_INVALID", None
    execution = await db.get(ProjectExecution, turn.execution_id, populate_existing=True)
    room = await db.get(Room, turn.room_id, populate_existing=True)
    if execution is None or room is None or room.project_id != execution.project_id or room.archived_at is not None:
        return False, "EXECUTION_SCOPE_REVOKED", None
    if execution.input_revision != turn.execution_input_revision:
        return False, "EXECUTION_INPUT_SUPERSEDED", None
    if execution.status not in ACTIVE_STATES:
        return False, "EXECUTION_NOT_ACTIVE", None
    now = datetime.now(UTC)
    if execution.deadline_at is not None and execution.deadline_at <= now:
        return False, "EXECUTION_DEADLINE_REACHED", None
    unresolved = await db.scalar(select(ExecutionStop.id).where(
        ExecutionStop.execution_id == execution.id,
        ExecutionStop.status.not_in(SAFE_STOP_STATES),
    ).limit(1))
    if unresolved:
        return False, "EXECUTION_STOP_UNCONFIRMED", None
    if lock:
        changed = await db.scalar(update(ProjectExecution).where(
            ProjectExecution.id == execution.id,
            ProjectExecution.input_revision == turn.execution_input_revision,
            ProjectExecution.status.in_(ACTIVE_STATES),
            or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > now),
            exists(select(Room.id).where(Room.id == ProjectExecution.operating_room_id,
                Room.project_id == ProjectExecution.project_id, Room.archived_at.is_(None))),
            ~exists(select(ExecutionStop.id).where(ExecutionStop.execution_id == ProjectExecution.id,
                ExecutionStop.status.not_in(SAFE_STOP_STATES))),
        ).values(state_revision=ProjectExecution.state_revision,
                 updated_at=ProjectExecution.updated_at).returning(ProjectExecution.id))
        if changed is None:
            return False, "EXECUTION_AUTHORIZATION_CHANGED", None
    revision = await db.scalar(select(ExecutionInputRevision).where(
        ExecutionInputRevision.execution_id == execution.id,
        ExecutionInputRevision.revision == turn.execution_input_revision,
    ))
    if revision is None:
        return False, "EXECUTION_INPUT_MISSING", None
    from anygarden.project_executions.service import ExecutionConflict

    try:
        payload = frozen_input_payload(execution, revision)
    except ExecutionConflict:
        return False, "INPUT_SNAPSHOT_TOO_LARGE", None
    return True, None, payload


def disposition(execution: ProjectExecution, input_revision: int | None) -> tuple[bool, str]:
    if input_revision != execution.input_revision:
        return False, "superseded"
    if execution.status in {"cancelling", "cancelled"}:
        return False, execution.status
    if execution.status == "revising":
        return False, "current"
    return True, "current"
