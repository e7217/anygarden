"""Consume an explicit approval before one server-managed HTTP POST."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi import HTTPException
from sqlalchemy import exists, select, update

from anygarden.db.execution_approval_models import ExecutionApproval
from anygarden.db.models import ProjectExecution, ProjectExecutionEvent, Task
from anygarden.messages.service import append_message
from anygarden.project_executions.approvals import (
    _fence_execution,
    _now,
    _queue,
    _source_root,
    artifact_bytes,
    validate_current_approval,
)
from anygarden.project_executions.service import (
    ACTIVE_EXECUTION_STATES,
    ExecutionConflict,
    TurnProof,
    authorize_turn,
    require_execution_operation,
)

TransitionCallback = Callable[[str, str], Awaitable[None]]
log = logging.getLogger(__name__)


async def _notify(callback: TransitionCallback | None, row: ExecutionApproval) -> None:
    if callback is not None:
        try:
            await asyncio.wait_for(callback(row.id, row.task_id), timeout=2)
        except Exception:  # noqa: BLE001 — only fanout; never retry a committed action
            # The committed permit/outcome is authoritative. A fanout failure
            # must never trigger another POST or conceal a recorded receipt.
            log.warning("approved_action_fanout_failed", extra={"approval_id": row.id, "task_id": row.task_id})


async def _record_outcome(db, row: ExecutionApproval, *, status: str, receipt: dict, error: str | None) -> bool:
    changed = await db.scalar(update(ExecutionApproval).where(
        ExecutionApproval.id == row.id, ExecutionApproval.status == "executing",
        ExecutionApproval.action_digest == row.action_digest,
    ).values(status=status, receipt=receipt, error=error, finished_at=_now()).returning(ExecutionApproval.id))
    if changed is None:
        return False
    await db.refresh(row)
    if status != "succeeded":
        await db.execute(update(Task).where(
            Task.id == row.task_id, Task.execution_id == row.execution_id,
            Task.input_revision == row.input_revision, Task.status == "in_progress",
            Task.assignee_participant_id == row.requester_participant_id,
            exists(select(ProjectExecution.id).where(
                ProjectExecution.id == row.execution_id,
                ProjectExecution.input_revision == row.input_revision,
                ProjectExecution.status.in_(ACTIVE_EXECUTION_STATES),
            )),
        ).values(status="blocked", error="승인된 실행 결과를 확인할 수 없습니다. 자동 재전송하지 않습니다." if status == "unknown" else "승인된 실행이 실패했습니다. 전송을 반복하지 않습니다."))
    execution = await db.get(ProjectExecution, row.execution_id)
    message = None
    if execution is not None:
        # An outcome survives a task/lease ending during I/O; it is evidence
        # of the consumed permit, not permission to continue a changed task.
        try:
            async with db.begin_nested():
                message = await append_message(
                    db, row.operating_room_id, None,
                    f"승인된 실행 결과 · {row.task_title}\n\n대상: {row.target_label}\n"
                    f"상태: {status}\n파일 SHA-256: {row.artifact_sha256}\n"
                    + ("수신처의 성공 응답을 기록했습니다." if status == "succeeded" else "자동 재전송하지 않습니다. 실행 기록을 확인하세요."),
                    {"system_origin": "execution_approval_result", "ingest_only": True,
                     "execution_approval": {"id": row.id, "execution_id": row.execution_id,
                                            "task_id": row.task_id, "input_revision": row.input_revision, "status": status}},
                    thread_root_id=await _source_root(db, execution),
                )
        except (HTTPException, ExecutionConflict):
            # Archiving the room while the POST runs must not erase the
            # committed outcome or return a consumed permit to approved.
            message = None
        if message is not None:
            row.result_message_id = message.id
        db.add(ProjectExecutionEvent(execution_id=row.execution_id, task_id=row.task_id,
            event_key=f"approval:{row.id}:outcome", event_type="approved_action_finished",
            details={"approval_id": row.id, "status": status, "receipt": receipt, "error": error},
            message_id=message.id if message else None))
    await db.flush()
    if message is not None:
        _queue(db, message)
    return True


async def expire_executing_actions(db, *, now=None) -> list[ExecutionApproval]:
    """Mark interrupted sends unknown; never reset or resend their permit."""
    now = now or _now()
    rows = list(await db.scalars(select(ExecutionApproval).where(
        ExecutionApproval.status == "executing", ExecutionApproval.execution_deadline_at <= now,
    ).with_for_update()))
    changed = []
    for row in rows:
        receipt = {"idempotency_key": row.id, "request_sha256": row.payload_sha256, "target_alias": row.target_alias}
        if await _record_outcome(db, row, status="unknown", receipt=receipt, error="ACTION_OUTCOME_UNKNOWN"):
            changed.append(row)
    return changed


async def execute_approved_action(
    session_factory, *, agent_id: str, proof: TurnProof, approval_id: str, targets: dict,
    artifact_files_dir: Path, on_transition: TransitionCallback | None = None,
) -> ExecutionApproval:
    """Commit consumption before I/O. Repeats read the receipt, never POST again."""
    async with session_factory() as db:
        turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
        row = await db.get(ExecutionApproval, approval_id, populate_existing=True, with_for_update=True)
        if row is None:
            raise ExecutionConflict("APPROVAL_NOT_FOUND", "Execution approval not found")
        if (row.requester_agent_id != agent_id or row.requester_participant_id != turn.target_participant_id
            or row.task_id != turn.task_id or row.task_room_id != turn.room_id):
            raise ExecutionConflict("APPROVAL_WORKER_MISMATCH", "Only the original action task worker can use this permit")
        if row.status in {"executing", "succeeded", "failed", "unknown"}:
            # Returning a persisted consumed permit conveys no permission to
            # send again, even when the old input has since been superseded.
            return row
        if row.status != "approved" or row.decision != "approve" or row.decided_by_user_id is None:
            raise ExecutionConflict("APPROVAL_NOT_APPROVED", "An explicit user approval is required before execution")
        if row.resume_message_id is None or turn.trigger_message_id != row.resume_message_id:
            raise ExecutionConflict("APPROVAL_CONTEXT_SUPERSEDED", "Use the fresh approval-bearing task continuation")
        execution = await db.get(ProjectExecution, row.execution_id)
        if execution is None:
            raise ExecutionConflict("APPROVAL_EXECUTION_CHANGED", "Execution is unavailable")
        await _fence_execution(db, execution, row.input_revision)
        execution, task, _, _, artifact = await validate_current_approval(db, row, targets=targets)
        require_execution_operation(execution, f"managed_{row.action_kind}")
        from anygarden.project_executions.requests import pending_for_task

        if await pending_for_task(db, task_id=task.id, input_revision=row.input_revision):
            raise ExecutionConflict("APPROVAL_TASK_WAITING_FOR_INPUT", "Resolve current task questions before executing the approved action")
        if task.status != "in_progress":
            raise ExecutionConflict("APPROVAL_TASK_NOT_CLAIMED", "Action task must be currently claimed")
        raw = artifact_bytes(artifact_files_dir, artifact)
        payload = json.loads(row.payload_json)
        if payload.get("artifact", {}).get("content_base64") != base64.b64encode(raw).decode("ascii"):
            raise ExecutionConflict("APPROVAL_ARTIFACT_CHANGED", "Frozen action content no longer matches the accepted artifact")
        changed = await db.scalar(update(ExecutionApproval).where(
            ExecutionApproval.id == row.id, ExecutionApproval.status == "approved",
            ExecutionApproval.decision == "approve", ExecutionApproval.action_digest == row.action_digest,
        ).values(status="executing", executing_turn_id=turn.request_id, executed_at=_now(),
                 execution_deadline_at=_now() + timedelta(seconds=45)).returning(ExecutionApproval.id))
        if changed is None:
            raise ExecutionConflict("APPROVAL_PERMIT_CONSUMED", "This permit was already consumed")
        await db.refresh(row)
        body = row.payload_json.encode("utf-8")
        url = row.target_url
        receipt = {"idempotency_key": row.id, "request_sha256": row.payload_sha256, "target_alias": row.target_alias}
        db.add(ProjectExecutionEvent(execution_id=row.execution_id, task_id=row.task_id,
            event_key=f"approval:{row.id}:execute", event_type="approved_action_started",
            details={"approval_id": row.id, "action_digest": row.action_digest, "payload_sha256": row.payload_sha256}))
        await db.commit()
    await _notify(on_transition, row)
    status, error = "unknown", "ACTION_OUTCOME_UNKNOWN"
    cancelled = False
    try:
        async with asyncio.timeout(25):
            async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", url, content=body,
                    headers={"Content-Type": "application/json", "Idempotency-Key": approval_id}) as response:
                    response_hash = hashlib.sha256()
                    size = 0
                    async for chunk in response.aiter_bytes():
                        response_hash.update(chunk)
                        size += len(chunk)
                    receipt.update(http_status=response.status_code, response_sha256=response_hash.hexdigest(), response_size_bytes=size)
                    if 200 <= response.status_code < 300:
                        status, error = "succeeded", None
                    elif 300 <= response.status_code < 500:
                        status, error = "failed", "ACTION_RECEIVER_REJECTED"
    except asyncio.CancelledError:
        cancelled = True
    except (httpx.HTTPError, TimeoutError, OSError):
        # No exception text or response body reaches logs/API: both can hold
        # credentials. Network ambiguity never restores an approved permit.
        pass

    async def finish():
        async with session_factory() as db:
            persisted = await db.get(ExecutionApproval, approval_id, populate_existing=True, with_for_update=True)
            if persisted is None:
                raise ExecutionConflict("APPROVAL_OUTCOME_UNAVAILABLE", "Consumed action outcome record is unavailable")
            await _record_outcome(db, persisted, status=status, receipt=receipt, error=error)
            await db.commit()
        await _notify(on_transition, persisted)
        return persisted

    completion = asyncio.create_task(finish())
    try:
        final_row = await asyncio.shield(completion)
    except asyncio.CancelledError:
        final_row = await completion
        cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return final_row
