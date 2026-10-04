"""Human decisions bound to an accepted artifact and a fixed managed action."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import exists, or_, select, update
from sqlalchemy.exc import IntegrityError

from anygarden.auth.dependencies import Identity
from anygarden.db.execution_approval_models import ExecutionApproval
from anygarden.db.models import (
    Agent,
    Message,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    RoomArtifact,
    Task,
    TaskBlocker,
    TaskResult,
)
from anygarden.messages.service import append_message, inject_task_assignment_message
from anygarden.project_executions.service import (
    ACTIVE_EXECUTION_STATES,
    ExecutionConflict,
    TurnProof,
    authorize_turn,
)
from anygarden.rooms.authorization import (
    AGENT_EXECUTION_ROLES,
    Capability,
    require_capability,
)
from anygarden.task_service import TaskMutationConflict, claim_task_cas


def _now() -> datetime:
    return datetime.now(UTC)


def _text(value: str, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ExecutionConflict("APPROVAL_TEXT_INVALID", f"{name} must contain 1 to {limit} characters")
    return value.strip()


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def configured_target(targets: dict, alias: str, kind: str) -> dict:
    target = targets.get(alias) if isinstance(targets, dict) else None
    if not isinstance(target, dict) or kind not in {"submission", "deployment"} or target.get("action_kind") != kind:
        raise ExecutionConflict("APPROVAL_TARGET_NOT_CONFIGURED", "The server has not configured this action and target alias")
    url = target.get("url")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
                 and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ExecutionConflict("APPROVAL_TARGET_INVALID", "Configured target must be an HTTP URL without credentials, query, or fragment")
    return {"url": url, "label": _text(target.get("label") or alias, "target label", 500), "action_kind": kind}


def action_digest(*, target_url: str, action_kind: str, payload_sha256: str) -> str:
    return _sha(_canonical({"target_url": target_url, "action_kind": action_kind, "payload_sha256": payload_sha256}))


def artifact_bytes(artifact_files_dir: Path, artifact: RoomArtifact) -> bytes:
    """Read only the canonical artifact inode without following room/file symlinks."""
    try:
        if (str(UUID(artifact.room_id)) != artifact.room_id or str(UUID(artifact.id)) != artifact.id
            or artifact.storage_path != f"{artifact.room_id}/{artifact.id}"):
            raise ValueError
        base_fd = os.open(artifact_files_dir.resolve(), os.O_RDONLY | os.O_DIRECTORY)
        try:
            room_fd = os.open(artifact.room_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
            try:
                file_fd = os.open(artifact.id, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=room_fd)
                with os.fdopen(file_fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError
                    raw = stream.read(768 * 1024 + 1)
            finally:
                os.close(room_fd)
        finally:
            os.close(base_fd)
    except (OSError, ValueError):
        raise ExecutionConflict("APPROVAL_ARTIFACT_UNAVAILABLE", "Approved artifact bytes are unavailable or their storage path is unsafe") from None
    if len(raw) > 768 * 1024 or len(raw) != artifact.size_bytes or _sha(raw) != artifact.sha256:
        raise ExecutionConflict("APPROVAL_ARTIFACT_CHANGED", "Artifact bytes do not match the accepted size and SHA-256")
    return raw


def approval_payload(row: ExecutionApproval) -> dict:
    names = (
        "id", "execution_id", "task_id", "task_title", "operating_room_id", "task_room_id",
        "source_message_id", "input_revision", "source_task_id", "source_result_id",
        "source_result_version", "source_result_sha256", "artifact_id", "artifact_room_id",
        "artifact_sha256", "action_key", "action_kind", "target_alias", "target_label", "target_url",
        "summary", "action_digest", "payload_sha256", "status", "requester_agent_id",
        "requester_participant_id", "decision", "decided_by_user_id", "request_message_id",
        "decision_message_id", "resume_message_id", "result_message_id", "receipt", "error",
    )
    out = {name: getattr(row, name) for name in names}
    out["artifact_filename"] = row.artifact_filename
    out["artifact_url"] = f"/api/v1/rooms/{row.artifact_room_id}/artifacts/{row.artifact_id}"
    for name in ("created_at", "decided_at", "executed_at", "finished_at"):
        value = getattr(row, name)
        out[name] = value.isoformat() if value else None
    return out


async def blocking_for_task(db, *, task_id: str, input_revision: int | None = None) -> list[ExecutionApproval]:
    stmt = select(ExecutionApproval).where(ExecutionApproval.task_id == task_id, ExecutionApproval.status != "succeeded")
    if input_revision is not None:
        stmt = stmt.where(ExecutionApproval.input_revision == input_revision)
    return list(await db.scalars(stmt.order_by(ExecutionApproval.created_at, ExecutionApproval.id)))


async def pending_for_task(db, *, task_id: str, input_revision: int | None = None) -> list[ExecutionApproval]:
    stmt = select(ExecutionApproval).where(ExecutionApproval.task_id == task_id, ExecutionApproval.status == "pending")
    if input_revision is not None:
        stmt = stmt.where(ExecutionApproval.input_revision == input_revision)
    return list(await db.scalars(stmt.order_by(ExecutionApproval.created_at, ExecutionApproval.id)))


async def _fence_execution(db, execution: ProjectExecution, revision: int) -> None:
    changed = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == execution.id, ProjectExecution.input_revision == revision,
        ProjectExecution.status.in_(ACTIVE_EXECUTION_STATES),
        or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > _now()),
        exists(select(Room.id).where(Room.id == execution.operating_room_id,
            Room.project_id == execution.project_id, Room.archived_at.is_(None))),
    ).values(state_revision=ProjectExecution.state_revision + 1, updated_at=_now()).returning(ProjectExecution.id))
    if changed is None:
        raise ExecutionConflict("APPROVAL_EXECUTION_CHANGED", "Execution is inactive, expired, or its input revision changed")


async def _source_root(db, execution: ProjectExecution) -> str:
    source = await db.get(Message, execution.source_message_id)
    if source is None or source.room_id != execution.operating_room_id:
        raise ExecutionConflict("APPROVAL_SOURCE_INVALID", "Original operating-room request is unavailable")
    return source.root_message_id or source.id


def _queue(db, *messages: Message) -> None:
    db.info.setdefault("project_execution_messages", []).extend(messages)


async def _source_result(db, *, execution: ProjectExecution, source_task_id: str, version: int, artifact_id: str):
    source_task = await db.get(Task, source_task_id, populate_existing=True, with_for_update=True)
    result = await db.scalar(select(TaskResult).where(TaskResult.task_id == source_task_id, TaskResult.version == version)
                             .execution_options(populate_existing=True).with_for_update())
    artifact = await db.get(RoomArtifact, artifact_id, populate_existing=True, with_for_update=True)
    if (source_task is None or result is None or artifact is None
        or source_task.execution_id != execution.id or result.execution_id != execution.id
        or source_task.status != "done" or source_task.result_version != version
        or source_task.input_revision != execution.input_revision or result.input_revision != execution.input_revision):
        raise ExecutionConflict("APPROVAL_RESULT_STALE", "Approval requires the latest completed result in this execution and input revision")
    references = [ref for ref in result.artifacts if isinstance(ref, dict) and ref.get("artifact_id") == artifact_id]
    if len(references) != 1:
        raise ExecutionConflict("APPROVAL_ARTIFACT_NOT_ACCEPTED", "Artifact must be part of the specified accepted task result")
    from anygarden.project_executions.service import _validate_artifacts

    await _validate_artifacts(db, execution, source_task, references)
    return source_task, result, artifact


async def validate_current_approval(db, row: ExecutionApproval, *, targets: dict):
    """Validate frozen scope; the caller owns execution locking and transaction."""
    execution = await db.get(ProjectExecution, row.execution_id, populate_existing=True)
    task = await db.get(Task, row.task_id, populate_existing=True, with_for_update=True)
    room = await db.get(Room, row.task_room_id)
    ops = await db.get(Room, row.operating_room_id)
    participant = await db.get(Participant, row.requester_participant_id) if row.requester_participant_id else None
    agent = await db.get(Agent, row.requester_agent_id) if row.requester_agent_id else None
    if (execution is None or task is None or room is None or ops is None or participant is None
        or agent is None or agent.desired_state != "running"
        or execution.status not in ACTIVE_EXECUTION_STATES or execution.input_revision != row.input_revision
        or execution.operating_room_id != row.operating_room_id or execution.source_message_id != row.source_message_id
        or (execution.deadline_at is not None and execution.deadline_at <= _now())
        or room.archived_at is not None or ops.archived_at is not None
        or room.project_id != execution.project_id or ops.project_id != execution.project_id
        or task.execution_id != execution.id or task.room_id != row.task_room_id
        or task.input_revision != row.input_revision or task.status not in {"blocked", "in_progress"}
        or task.assignee_participant_id != row.requester_participant_id
        or participant.room_id != room.id or participant.agent_id != row.requester_agent_id
        or participant.role not in AGENT_EXECUTION_ROLES):
        raise ExecutionConflict("APPROVAL_SCOPE_STALE", "Original action task, worker, project, or input revision is no longer current")
    target = configured_target(targets, row.target_alias, row.action_kind)
    if target["url"] != row.target_url:
        raise ExecutionConflict("APPROVAL_TARGET_CHANGED", "Configured target changed after this approval was requested")
    _, result, artifact = await _source_result(db, execution=execution, source_task_id=row.source_task_id,
                                               version=row.source_result_version, artifact_id=row.artifact_id)
    if (result.id != row.source_result_id or result.result_sha256 != row.source_result_sha256
        or artifact.sha256 != row.artifact_sha256 or artifact.room_id != row.artifact_room_id
        or artifact.filename != row.artifact_filename or _sha(row.payload_json) != row.payload_sha256
        or action_digest(target_url=row.target_url, action_kind=row.action_kind, payload_sha256=row.payload_sha256) != row.action_digest):
        raise ExecutionConflict("APPROVAL_CONTENT_CHANGED", "Approval is bound to a different result, artifact, or action digest")
    return execution, task, participant, room, artifact


async def approval_is_current(db, row: ExecutionApproval, *, targets: dict) -> bool:
    try:
        await validate_current_approval(db, row, targets=targets)
    except ExecutionConflict:
        return False
    return True


async def request_approval(
    db, *, agent_id: str, proof: TurnProof, task_id: str, action_key: str, action_kind: str,
    target_alias: str, source_task_id: str, source_result_version: int, artifact_id: str,
    summary: str, targets: dict, artifact_files_dir: Path,
) -> ExecutionApproval:
    action_key = _text(action_key, "action_key", 160)
    summary = _text(summary, "summary", 20000)
    target_alias = _text(target_alias, "target_alias", 160)
    target = configured_target(targets, target_alias, action_kind)
    if type(source_result_version) is not int or source_result_version < 1:
        raise ExecutionConflict("APPROVAL_RESULT_VERSION_INVALID", "Source result version must be a positive integer")
    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    task = await db.get(Task, task_id, populate_existing=True, with_for_update=True)
    if (task is None or turn.task_id != task.id or turn.room_id != task.room_id
        or task.assignee_participant_id != turn.target_participant_id or not task.execution_id):
        raise ExecutionConflict("APPROVAL_TASK_NOT_CURRENT", "Approval must belong to this worker turn's task")
    execution = await db.get(ProjectExecution, task.execution_id)
    if execution is None or execution.input_revision != task.input_revision:
        raise ExecutionConflict("APPROVAL_INPUT_REVISION_STALE", "Action task input revision is no longer current")
    from anygarden.project_executions.service import require_execution_operation

    require_execution_operation(execution, f"managed_{action_kind}")
    scope = (ExecutionApproval.execution_id == execution.id, ExecutionApproval.task_id == task.id,
             ExecutionApproval.input_revision == task.input_revision, ExecutionApproval.action_key == action_key)
    existing = await db.scalar(select(ExecutionApproval).where(*scope))
    async with db.begin_nested():
        await _fence_execution(db, execution, task.input_revision)
        existing = await db.scalar(select(ExecutionApproval).where(*scope).execution_options(populate_existing=True))
        _, result, artifact = await _source_result(db, execution=execution, source_task_id=source_task_id,
                                                  version=source_result_version, artifact_id=artifact_id)
        raw = artifact_bytes(artifact_files_dir, artifact)
        aid = existing.id if existing else str(uuid4())
        payload = _canonical({
            "approval_id": aid, "execution_id": execution.id, "task_id": task.id,
            "input_revision": task.input_revision, "action_kind": action_kind,
            "source_task_id": source_task_id, "source_result_id": result.id,
            "source_result_version": result.version, "source_result_sha256": result.result_sha256,
            "artifact": {"id": artifact.id, "filename": artifact.filename, "mime": artifact.mime,
                         "sha256": artifact.sha256, "content_base64": base64.b64encode(raw).decode("ascii")},
        })
        payload_hash = _sha(payload)
        digest = action_digest(target_url=target["url"], action_kind=action_kind, payload_sha256=payload_hash)
        if existing is not None:
            if existing.action_digest != digest or existing.summary != summary or existing.target_alias != target_alias:
                raise ExecutionConflict("APPROVAL_KEY_REUSED", "This action key already refers to a different immutable action")
            return existing
        from anygarden.project_executions.service import assert_task_workflow_ready

        await assert_task_workflow_ready(db, task=task, proof=proof, operation="request_approval")
        if task.status != "in_progress":
            raise ExecutionConflict("APPROVAL_TASK_NOT_CLAIMED", "Only a currently claimed worker task can request approval")
        changed = await db.scalar(update(Task).where(
            Task.id == task.id, Task.execution_id == execution.id, Task.input_revision == execution.input_revision,
            Task.status == "in_progress", Task.assignee_participant_id == turn.target_participant_id,
            exists(select(Agent.id).where(Agent.id == agent_id, Agent.generation == proof.generation)),
        ).values(status="blocked", error="운영실에서 실행 승인을 기다리고 있습니다.").returning(Task.id))
        if changed is None:
            raise ExecutionConflict("APPROVAL_TASK_CHANGED", "Action task changed before approval was requested")
        row = ExecutionApproval(
            id=aid, execution_id=execution.id, task_id=task.id, task_title=task.title,
            operating_room_id=execution.operating_room_id, task_room_id=task.room_id,
            source_message_id=execution.source_message_id, input_revision=task.input_revision,
            source_task_id=source_task_id, source_result_id=result.id, source_result_version=result.version,
            source_result_sha256=result.result_sha256, artifact_id=artifact.id, artifact_room_id=artifact.room_id,
            artifact_filename=artifact.filename, artifact_sha256=artifact.sha256,
            action_key=action_key, action_kind=action_kind, target_alias=target_alias, target_label=target["label"],
            target_url=target["url"], summary=summary, action_digest=digest,
            payload_json=payload, payload_sha256=payload_hash, status="pending",
            requester_agent_id=agent_id, requester_participant_id=turn.target_participant_id,
            turn_request_id=turn.request_id,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # The execution fence serializes same-execution requests; transport
            # retries normally find the existing row before this insert.
            raise ExecutionConflict("APPROVAL_REQUEST_CONFLICT", "An approval for this action key already exists") from None
        message = await append_message(
            db, execution.operating_room_id, None,
            f"실행 승인이 필요합니다 · {task.title}\n\n{summary}\n\n"
            f"대상: {row.target_label}\n{row.target_url}\n파일: {row.artifact_filename}\n"
            "승인하면 이 파일을 지정된 수신처에 한 번 전송합니다. 승인 전이나 거절 시에는 전송하지 않습니다.",
            {"system_origin": "execution_approval", "ingest_only": True,
             "execution_approval": {"id": row.id, "execution_id": execution.id, "task_id": task.id,
                                    "input_revision": row.input_revision, "status": "pending"}},
            thread_root_id=await _source_root(db, execution),
        )
        row.request_message_id = message.id
        db.add(ProjectExecutionEvent(execution_id=execution.id, task_id=task.id,
            event_key=f"approval:{row.id}:request", event_type="approval_requested",
            details={"approval_id": row.id, "action_digest": row.action_digest}, message_id=message.id))
        await db.flush()
    _queue(db, message)
    return row


async def decide(db, *, useridentity: Identity, approval_id: str, decision: str, targets: dict) -> ExecutionApproval:
    if useridentity.kind != "user":
        raise HTTPException(403, "Only an authorized registered user can decide an action approval")
    if decision not in {"approve", "reject"}:
        raise ExecutionConflict("APPROVAL_DECISION_INVALID", "Decision must be approve or reject")
    row = await db.get(ExecutionApproval, approval_id)
    if row is None:
        raise HTTPException(404, "Execution approval not found")
    access = await require_capability(db, room_id=row.operating_room_id, identity=useridentity, capability=Capability.MESSAGE_SEND)
    execution = await db.get(ProjectExecution, row.execution_id)
    if execution is None or access.room.project_id != execution.project_id:
        raise HTTPException(404, "Execution approval not found")
    if row.decision is not None:
        if row.decision == decision:
            return row
        raise ExecutionConflict("APPROVAL_ALREADY_DECIDED", "This approval already has a different explicit decision")
    messages = []
    async with db.begin_nested():
        await _fence_execution(db, execution, row.input_revision)
        row = await db.get(ExecutionApproval, approval_id, populate_existing=True, with_for_update=True)
        if row.decision is not None:
            if row.decision == decision:
                return row
            raise ExecutionConflict("APPROVAL_ALREADY_DECIDED", "This approval already has a different explicit decision")
        execution, task, participant, room, _ = await validate_current_approval(db, row, targets=targets)
        if task.status != "blocked" or row.status != "pending":
            raise ExecutionConflict("APPROVAL_TASK_NOT_WAITING", "Original task is no longer waiting for this approval")
        updated = await db.scalar(update(ExecutionApproval).where(
            ExecutionApproval.id == row.id, ExecutionApproval.status == "pending",
            ExecutionApproval.decision.is_(None), ExecutionApproval.action_digest == row.action_digest,
        ).values(status="approved" if decision == "approve" else "rejected", decision=decision,
                 decided_by_user_id=useridentity.id, decided_at=_now()).returning(ExecutionApproval.id))
        if updated is None:
            raise ExecutionConflict("APPROVAL_DECISION_CONFLICT", "Approval changed before this decision was recorded")
        await db.refresh(row)
        task.spec = (task.spec or "") + (
            f"\n\n**OPERATING ROOM APPROVAL · {row.id} · input revision {row.input_revision}**\n"
            f"Decision: {decision}\nAction digest: {row.action_digest}\n"
            f"Target alias: {row.target_alias}\nArtifact: {row.artifact_id} / SHA-256 {row.artifact_sha256}\n"
            "승인된 실행은 execute_approved_project_action 도구로만 수행합니다. 성공 영수증 전에는 완료하지 마세요."
        )
        task.error = "사용자가 실행 승인을 거절했습니다. 전송하지 않습니다." if decision == "reject" else None
        message = await append_message(
            db, row.operating_room_id, access.participant.id if access.participant else None,
            f"실행 승인 {'허용' if decision == 'approve' else '거절'} · {row.task_title}\n\n{row.summary}",
            {"system_origin": "execution_approval_decision", "ingest_only": True,
             "execution_approval": {"id": row.id, "execution_id": row.execution_id, "task_id": row.task_id,
                                    "input_revision": row.input_revision, "status": row.status}},
            thread_root_id=await _source_root(db, execution),
        )
        row.decision_message_id = message.id
        messages.append(message)
        if decision == "approve":
            from anygarden.project_executions.requests import (
                pending_for_task as pending_questions,
            )

            others = await db.scalar(select(ExecutionApproval.id).where(
                ExecutionApproval.task_id == task.id, ExecutionApproval.input_revision == row.input_revision,
                ExecutionApproval.id != row.id, ExecutionApproval.status != "succeeded",
            ).limit(1))
            dependencies = await db.scalar(select(TaskBlocker.task_id).where(TaskBlocker.task_id == task.id).limit(1))
            if others or dependencies or await pending_questions(db, task_id=task.id, input_revision=row.input_revision):
                raise ExecutionConflict("APPROVAL_OTHER_WAITING", "Resolve other waiting input or dependencies before approving this action")
            task.status = "todo"
            await db.flush()
            try:
                task = await claim_task_cas(db, task_id=task.id, room_id=task.room_id, participant_id=participant.id)
            except TaskMutationConflict as exc:
                raise ExecutionConflict("APPROVAL_TASK_CHANGED", exc.detail) from None
            resume = await inject_task_assignment_message(db, room=room, task=task, sender_participant_id=None, event="reassigned")
            row.resume_message_id = resume.id
            messages.append(resume)
        db.add(ProjectExecutionEvent(execution_id=execution.id, task_id=task.id,
            event_key=f"approval:{row.id}:decision", event_type="approval_decided",
            details={"approval_id": row.id, "decision": decision, "action_digest": row.action_digest}, message_id=message.id))
        await db.flush()
    _queue(db, *messages)
    return row
