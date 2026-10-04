"""User-requested whole-run revisions and cancellation, with durable stop gates."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists, or_, select, update

from anygarden.db.models import (
    Agent,
    ExecutionInputRevision,
    ExecutionMutation,
    Message,
    Participant,
    ProjectExecution,
    Room,
    RoomSharedFile,
    Task,
)
from anygarden.messages.service import append_message
from anygarden.project_executions.authorization import (
    ACTIVE_STATES,
    frozen_input_payload,
)
from anygarden.project_executions.service import (
    ExecutionConflict,
    _event,
    _now,
    _queue_message,
    delegation_total_expression,
)
from anygarden.project_executions.stop_service import (
    fence_execution_turns,
    settle_execution_mutation,
)
from anygarden.rooms.authorization import Capability, require_capability


def mutation_payload(row: ExecutionMutation) -> dict:
    names = ("id", "operation_id", "action", "phase", "reason", "previous_input_revision",
        "input_revision", "requested_by_user_id", "source_message_id", "root_task_id")
    result = {name: getattr(row, name) for name in names}
    result.update(requested_at=row.requested_at.isoformat(),
        completed_at=row.completed_at.isoformat() if row.completed_at else None)
    return result


def can_manage_execution(access, execution: ProjectExecution) -> bool:
    return bool(access.identity.kind == "user" and not access.is_archived
        and access.room.project_id == execution.project_id
        and (access.is_global_admin or access.effective_role in {"admin", "owner"}
            or (access.participant is not None and access.effective_role == "member"
                and execution.owner_user_id == access.identity.id)))


async def authorize_mutation(db, *, identity, execution_id: str):
    execution = await db.get(ProjectExecution, execution_id, populate_existing=True)
    if execution is None:
        raise HTTPException(404, "Project execution not found")
    access = await require_capability(db, room_id=execution.operating_room_id,
        identity=identity, capability=Capability.TASK_READ)
    if not can_manage_execution(access, execution):
        raise HTTPException(403, "Execution changes require an operating-room admin/owner or the original requesting member")
    return execution, access


def _request_digest(action: str, body: dict) -> str:
    try:
        if str(UUID(body["operation_id"])) != body["operation_id"]:
            raise ValueError
        for name, minimum in (("expected_input_revision", 1), ("expected_state_revision", 0)):
            if type(body[name]) is not int or body[name] < minimum:
                raise ValueError
        if not isinstance(body["reason"], str) or not body["reason"].strip() or len(body["reason"]) > 10000:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise ExecutionConflict("MUTATION_INPUT_INVALID", "A canonical operation ID, current revisions and a reason are required") from None
    return hashlib.sha256(json.dumps({"action": action, "body": body}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


async def _existing_operation(db, execution, body: dict, digest: str):
    existing = await db.scalar(select(ExecutionMutation).where(
        ExecutionMutation.execution_id == execution.id, ExecutionMutation.operation_id == body["operation_id"],
    ))
    if existing is not None and existing.request_sha256 != digest:
        raise ExecutionConflict("MUTATION_OPERATION_CONFLICT", "The operation ID was already used with a different request body")
    return existing


async def _file_snapshots(db, *, room_id: str, requested: list[dict], root: Path) -> list[dict]:
    snapshots, seen = [], set()
    for item in requested:
        file_id = item.get("file_id")
        try:
            if str(UUID(file_id)) != file_id or file_id in seen or str(UUID(room_id)) != room_id:
                raise ValueError
        except (ValueError, TypeError):
            raise ExecutionConflict("INPUT_FILE_SCOPE_INVALID", "Input file IDs must be unique canonical operating-room files") from None
        seen.add(file_id)
        row = await db.get(RoomSharedFile, file_id, populate_existing=True, with_for_update=True)
        if row is None or row.room_id != room_id or row.storage_path != f"{room_id}/{file_id}":
            raise ExecutionConflict("INPUT_FILE_SCOPE_INVALID", "Input files must belong to this execution's operating room")
        if item.get("sha256") != row.sha256:
            raise ExecutionConflict("INPUT_FILE_CHANGED", "The selected input file version changed; reload before revising")
        try:
            root_fd = os.open(root.resolve(), os.O_RDONLY | os.O_DIRECTORY)
            try:
                room_fd = os.open(room_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    file_fd = os.open(file_id, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=room_fd)
                    with os.fdopen(file_fd, "rb") as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise ValueError
                        raw = stream.read(256 * 1024 + 1)
                finally:
                    os.close(room_fd)
            finally:
                os.close(root_fd)
        except (OSError, ValueError):
            raise ExecutionConflict("INPUT_FILE_UNAVAILABLE", "Selected input bytes are unavailable or their storage path is unsafe") from None
        if len(raw) > 256 * 1024 or len(raw) != row.size_bytes or hashlib.sha256(raw).hexdigest() != row.sha256:
            raise ExecutionConflict("INPUT_FILE_CHANGED", "Selected input bytes do not match their immutable hash and size")
        try:
            contents = {"content": raw.decode("utf-8")}
        except UnicodeError:
            contents = {"content_base64": base64.b64encode(raw).decode("ascii")}
        snapshots.append({"file_id": row.id, "room_id": room_id, "filename": row.filename,
            "sha256": row.sha256, "size_bytes": row.size_bytes, **contents})
    return snapshots


async def _claim_mutation(db, *, execution, access, action: str, body: dict, digest: str,
    objective: str | None = None, criteria: list | None = None) -> ExecutionMutation:
    expected = body["expected_input_revision"]
    next_revision = expected + 1 if action == "revise" else expected
    allowed = ACTIVE_STATES if action == "revise" else ACTIVE_STATES | {"revising"}
    values = {"status": "revising" if action == "revise" else "cancelling",
        "state_revision": ProjectExecution.state_revision + 1, "updated_at": _now(),
        "delegation_count": delegation_total_expression(execution.id)}
    if action == "revise":
        values.update(input_revision=next_revision, objective=objective, completion_criteria=criteria,
            plan_sealed=False, required_task_ids=[], final_report_message_id=None,
            error=None, finished_at=None)
    changed = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == execution.id, ProjectExecution.input_revision == expected,
        ProjectExecution.state_revision == body["expected_state_revision"],
        ProjectExecution.status.in_(allowed),
        exists(select(Room.id).where(Room.id == ProjectExecution.operating_room_id,
            Room.project_id == ProjectExecution.project_id, Room.archived_at.is_(None))),
        True if access.is_global_admin else exists(select(Participant.id).where(
            Participant.room_id == ProjectExecution.operating_room_id,
            Participant.user_id == access.identity.id,
            or_(Participant.role.in_({"admin", "owner"}),
                (Participant.role == "member") & (ProjectExecution.owner_user_id == access.identity.id)),
        )),
    ).values(**values).returning(ProjectExecution.id))
    if changed is None:
        # A contender may have committed precisely the same operation while
        # we waited for its execution-row lock. No second intent is inserted.
        existing = await _existing_operation(db, execution, body, digest)
        if existing is not None:
            return existing
        raise ExecutionConflict("EXECUTION_MUTATION_CONFLICT", "Execution input/state changed or is inactive; reload before changing it")
    await db.execute(update(ExecutionMutation).where(ExecutionMutation.execution_id == execution.id,
        ExecutionMutation.phase == "awaiting_stop").values(phase="superseded", completed_at=_now()))
    mutation = ExecutionMutation(execution_id=execution.id, operation_id=body["operation_id"],
        request_sha256=digest, action=action, previous_input_revision=expected,
        input_revision=next_revision, expected_state_revision=body["expected_state_revision"],
        requested_by_user_id=access.identity.id, reason=body["reason"].strip())
    db.add(mutation)
    await db.flush()
    await db.refresh(execution)
    return mutation


async def _mutation_message(db, execution, access, mutation, content: str) -> Message:
    original = await db.get(Message, execution.source_message_id)
    message = await append_message(db, execution.operating_room_id,
        access.participant.id if access.participant else None, content,
        {"ingest_only": True, "system_origin": "execution_mutation",
            "execution_id": execution.id, "input_revision": mutation.input_revision,
            "mutation_id": mutation.id, "operation_id": mutation.operation_id,
            "actor_user_id": access.identity.id},
        thread_root_id=(original.root_message_id or original.id) if original else None)
    mutation.source_message_id = message.id
    _queue_message(db, message)
    return message


async def revise_execution(db, *, identity, execution_id: str, body: dict, room_files_dir: Path):
    execution, access = await authorize_mutation(db, identity=identity, execution_id=execution_id)
    digest = _request_digest("revise", body)
    existing = await _existing_operation(db, execution, body, digest)
    if existing is not None:
        return execution, existing
    if (body.get("change_scope") != "all" or not isinstance(body.get("objective"), str)
        or not body["objective"].strip() or len(body["objective"]) > 100000
        or not isinstance(body.get("constraints"), str) or len(body["constraints"]) > 100000
        or not isinstance(body.get("completion_criteria"), list)
        or any(not isinstance(item, str) for item in body["completion_criteria"])
        or not isinstance(body.get("input_files"), list)):
        raise ExecutionConflict("MUTATION_INPUT_INVALID", "Provide the whole objective, constraints, criteria and selected input files")
    lead = await db.get(Agent, execution.lead_agent_id)
    participant = await db.scalar(select(Participant).where(Participant.room_id == execution.operating_room_id,
        Participant.agent_id == execution.lead_agent_id))
    if lead is None or lead.desired_state != "running" or participant is None or participant.role not in {"member", "admin", "owner"}:
        raise ExecutionConflict("REVISION_LEAD_UNAVAILABLE", "The revised request requires its running operating-room lead")
    snapshots = await _file_snapshots(db, room_id=execution.operating_room_id,
        requested=body["input_files"], root=room_files_dir)
    revision = ExecutionInputRevision(execution_id=execution.id, revision=body["expected_input_revision"] + 1,
        objective=body["objective"].strip(), user_constraints=body["constraints"], input_files=snapshots,
        completion_criteria=list(body["completion_criteria"]), actor_user_id=identity.id,
        reason=body["reason"].strip(), change_scope="all")
    frozen_input_payload(execution, revision)
    mutation = await _claim_mutation(db, execution=execution, access=access, action="revise", body=body,
        digest=digest, objective=revision.objective, criteria=revision.completion_criteria)
    if mutation.source_message_id is not None:
        return execution, mutation
    message = await _mutation_message(db, execution, access, mutation,
        f"요청 변경 · 입력 revision {revision.revision}\n\n목표: {revision.objective}\n"
        f"제약: {revision.user_constraints}\n\n변경 이유: {mutation.reason}\n변경 범위: 전체 재계획. 이전 작업의 실제 중지 확인을 기다립니다.")
    root = Task(room_id=execution.operating_room_id, source_message_id=message.id,
        execution_id=execution.id, input_revision=revision.revision, title=revision.objective[:500],
        role="orchestration", required_for_execution=True, status="in_progress",
        assignee_participant_id=participant.id, created_by=identity.id, triggered_by="project_execution",
        assigned_at=_now(), started_at=_now(), goal_id=None,
        spec=f"Project execution {execution.id}; input revision {revision.revision}.\nObjective:\n{revision.objective}\n"
            f"Constraints:\n{revision.user_constraints}\nCompletion criteria:\n{revision.completion_criteria}\n"
            "The user changed the request. Replan all required work from this frozen revision; previous results are history. "
            "Seal the required plan and complete_project_execution only when its actual results and QA pass.")
    for item in snapshots:
        root.spec += f"\n\nInput file {item['filename']} (SHA-256 {item['sha256']}):\n" + item.get("content", f"Base64 original bytes: {item.get('content_base64', '')}")
    db.add(root)
    await db.flush()
    revision.source_message_id, revision.root_task_id = message.id, root.id
    execution.root_task_id, mutation.root_task_id = root.id, root.id
    db.add(revision)
    await _event(db, execution, f"mutation:{mutation.id}:requested", "execution_revision_requested",
        task_id=root.id, details={"mutation_id": mutation.id, "previous_input_revision": mutation.previous_input_revision,
            "input_revision": revision.revision, "change_scope": "all", "reason": mutation.reason})
    await fence_execution_turns(db, execution=execution, mutation=mutation)
    await settle_execution_mutation(db, execution.id)
    await db.flush()
    return execution, mutation


async def cancel_execution(db, *, identity, execution_id: str, body: dict):
    execution, access = await authorize_mutation(db, identity=identity, execution_id=execution_id)
    digest = _request_digest("cancel", body)
    existing = await _existing_operation(db, execution, body, digest)
    if existing is not None:
        return execution, existing
    mutation = await _claim_mutation(db, execution=execution, access=access, action="cancel", body=body, digest=digest)
    if mutation.source_message_id is not None:
        return execution, mutation
    mutation.root_task_id = execution.root_task_id
    await _mutation_message(db, execution, access, mutation,
        f"실행 취소 요청 · {execution.objective}\n\n이유: {mutation.reason}\n"
        "새 위임과 모델 시작을 차단했습니다. 진행 중 프로세스의 중지 확인과 이미 발생한 외부 효과는 별도로 보존합니다.")
    await _event(db, execution, f"mutation:{mutation.id}:requested", "execution_cancel_requested",
        task_id=mutation.root_task_id, details={"mutation_id": mutation.id, "input_revision": mutation.input_revision,
            "reason": mutation.reason, "native_engine_egress_controlled": False})
    await fence_execution_turns(db, execution=execution, mutation=mutation)
    await settle_execution_mutation(db, execution.id)
    await db.flush()
    return execution, mutation
