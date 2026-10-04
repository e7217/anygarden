"""Read published text bytes through the invocation's exact execution scope."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    Task,
    TaskResult,
)
from anygarden.project_executions.approvals import artifact_bytes
from anygarden.project_executions.service import (
    ExecutionConflict,
    TurnProof,
    _active,
    _validate_artifacts,
    authorize_turn,
    get_bound_task_execution_detail,
)
from anygarden.rooms.artifacts import ARTIFACT_MAX_BYTES, get_artifact

MAX_CHUNK_CHARACTERS = 12000
DEFAULT_CHUNK_CHARACTERS = 3000
TEXT_MIME_TYPES = frozenset({
    "text/plain", "text/markdown", "text/csv", "application/json", "text/html",
})


async def _publications(
    db: AsyncSession, execution: ProjectExecution, source: Task,
) -> AsyncIterator[tuple[str, dict]]:
    events = await db.scalars(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution.id,
        ProjectExecutionEvent.task_id == source.id,
        ProjectExecutionEvent.event_type == "artifact_published",
    ).order_by(ProjectExecutionEvent.created_at, ProjectExecutionEvent.id))
    for event in events:
        details = event.details if isinstance(event.details, dict) else {}
        references = details.get("artifacts")
        if not isinstance(references, list):
            continue
        for reference in references:
            if (isinstance(reference, dict)
                and reference.get("task_id") == source.id
                and reference.get("execution_id") == execution.id
                and reference.get("input_revision") == execution.input_revision):
                yield event.id, reference


async def _accepted_result(
    db: AsyncSession, execution: ProjectExecution, source: Task, reference: dict,
) -> TaskResult | None:
    if source.status != "done" or not source.result_version:
        return None
    result = await db.scalar(select(TaskResult).where(
        TaskResult.task_id == source.id,
        TaskResult.execution_id == execution.id,
        TaskResult.input_revision == execution.input_revision,
        TaskResult.version == source.result_version,
    ))
    if result is None or reference not in (result.artifacts or []):
        return None
    if sha256(result.result_markdown.encode("utf-8")).hexdigest() != result.result_sha256:
        raise ExecutionConflict("ARTIFACT_READ_RESULT_INVALID", "Accepted result content does not match its recorded SHA-256")
    return result


async def read_project_artifact(
    db: AsyncSession, *, agent_id: str, proof: TurnProof, task_id: str,
    artifact_id: str, artifact_files_dir: Path, offset: int = 0,
    limit: int = DEFAULT_CHUNK_CHARACTERS,
) -> dict:
    """Authorize a publication before reading its canonical, SHA-checked inode.

    A worker can read its own publications, exact accepted prerequisites and
    server-handed-off accepted direct children. A current root lead can additionally
    review this input revision's publications, including unfinished outputs.
    Room membership alone never grants access to another task's artifacts.
    Offsets count decoded Unicode characters rather than bytes.
    """
    if (type(offset) is not int or offset < 0 or type(limit) is not int
        or not 1 <= limit <= MAX_CHUNK_CHARACTERS):
        raise ExecutionConflict(
            "ARTIFACT_READ_RANGE_INVALID",
            f"offset must be a nonnegative integer and limit must be 1 to {MAX_CHUNK_CHARACTERS} characters",
        )
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ExecutionConflict("ARTIFACT_READ_ID_INVALID", "Use a published artifact ID")
    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    task = await db.get(Task, task_id)
    execution = await db.get(ProjectExecution, task.execution_id) if task and task.execution_id else None
    room = await db.get(Room, task.room_id) if task else None
    if (task is None or execution is None or turn.task_id != task.id
        or turn.execution_id != execution.id
        or turn.execution_input_revision != execution.input_revision
        or task.input_revision != execution.input_revision
        or turn.target_participant_id != task.assignee_participant_id
        or turn.room_id != task.room_id or room is None
        or room.project_id != execution.project_id or room.archived_at is not None):
        raise ExecutionConflict(
            "ARTIFACT_READ_TASK_NOT_BOUND",
            "Read artifacts only through your current leased execution task",
        )
    _active(execution)
    lead = task.id == execution.root_task_id and agent_id == execution.lead_agent_id
    candidates = []
    if not lead:
        view = await get_bound_task_execution_detail(db, execution_id=execution.id, task_id=task.id)
        for dependency in view["dependency_results"]:
            source = await db.get(Task, dependency["task_id"])
            result = await db.get(TaskResult, dependency["result_id"])
            if source is None or result is None:
                raise ExecutionConflict("ARTIFACT_READ_RESULT_INVALID", "The accepted prerequisite is unavailable")
            for reference in dependency["artifacts"] or []:
                if isinstance(reference, dict) and reference.get("artifact_id") == artifact_id:
                    candidates.append((source, reference, result, "accepted_prerequisite"))
        for child_input in view.get("child_results", []):
            source = await db.get(Task, child_input["task_id"])
            result = await db.get(TaskResult, child_input["result_id"])
            if source is None or result is None:
                raise ExecutionConflict("ARTIFACT_READ_RESULT_INVALID", "The handed-off direct child is unavailable")
            for reference in child_input["artifacts"] or []:
                if isinstance(reference, dict) and reference.get("artifact_id") == artifact_id:
                    candidates.append((source, reference, result, "accepted_direct_child"))
        from anygarden.project_executions.qa_repairs import repair_intent_for_turn

        intent = await repair_intent_for_turn(db, turn=turn)
        if intent is not None:
            feedback = await db.get(TaskResult, intent["qa_result_id"])
            source = await db.get(Task, intent["qa_task_id"])
            if (feedback is None or source is None or source.role != "qa"
                or source.status != "done" or source.result_version != intent["qa_result_version"]
                or feedback.task_id != source.id or feedback.execution_id != execution.id
                or feedback.input_revision != execution.input_revision
                or feedback.version != intent["qa_result_version"]
                or feedback.result_sha256 != intent["qa_result_sha256"]
                or sha256(feedback.result_markdown.encode()).hexdigest() != feedback.result_sha256
                or (feedback.verification or {}).get("verdict") != "fail"):
                raise ExecutionConflict("ARTIFACT_READ_REPAIR_FEEDBACK_INVALID", "Reserved failure feedback is unavailable or changed")
            for reference in feedback.artifacts or []:
                if isinstance(reference, dict) and reference.get("artifact_id") == artifact_id:
                    candidates.append((source, reference, feedback, "repair_feedback"))
    sources = [task]
    if lead:
        sources = list(await db.scalars(select(Task).join(Room, Room.id == Task.room_id).where(
            Task.execution_id == execution.id,
            Task.input_revision == execution.input_revision,
            Room.project_id == execution.project_id,
            Room.archived_at.is_(None),
        ).order_by(Task.created_at, Task.id)))
    for source in sources:
        async for _, reference in _publications(db, execution, source):
            if reference.get("artifact_id") == artifact_id:
                result = await _accepted_result(db, execution, source, reference)
                scope = "own_publication" if source.id == task.id else "lead_publication"
                candidates.append((source, reference, result, scope))
    if not candidates:
        raise ExecutionConflict(
            "ARTIFACT_READ_NOT_AUTHORIZED",
            "The artifact is not your publication or an authorized current execution input",
        )
    source, reference, result, scope = candidates[0]
    source_room = await db.get(Room, source.room_id)
    artifact = await get_artifact(db, room_id=reference.get("room_id"), artifact_id=artifact_id)
    artifact_room = await db.get(Room, artifact.room_id) if artifact else None
    if (source.execution_id != execution.id or source.input_revision != execution.input_revision
        or source_room is None or source_room.project_id != execution.project_id
        or source_room.archived_at is not None or artifact is None
        or artifact_room is None or artifact_room.project_id != execution.project_id
        or artifact_room.archived_at is not None
        or reference.get("producer_agent_id") != artifact.produced_by_agent_id
        or reference.get("filename") != artifact.filename):
        raise ExecutionConflict("ARTIFACT_READ_PROVENANCE_INVALID", "The published artifact scope or metadata changed")
    await _validate_artifacts(db, execution, source, [reference])
    publication_id = None
    async for event_id, published in _publications(db, execution, source):
        if published == reference:
            publication_id = event_id
            break
    if publication_id is None:
        raise ExecutionConflict("ARTIFACT_READ_PROVENANCE_INVALID", "A current publication is required")
    if artifact.mime not in TEXT_MIME_TYPES:
        raise ExecutionConflict("ARTIFACT_READ_MIME_UNSUPPORTED", "Only published UTF-8 text artifacts can be read")
    if not 0 <= artifact.size_bytes <= ARTIFACT_MAX_BYTES:
        raise ExecutionConflict("ARTIFACT_READ_SIZE_INVALID", "Artifact size exceeds the server's publication limit")
    try:
        raw = await asyncio.to_thread(artifact_bytes, artifact_files_dir, artifact)
    except ExecutionConflict as exc:
        code = "ARTIFACT_READ_CHANGED" if exc.code == "APPROVAL_ARTIFACT_CHANGED" else "ARTIFACT_READ_UNAVAILABLE"
        raise ExecutionConflict(code, "Artifact bytes are unavailable, unsafe, or do not match their published size and SHA-256") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ExecutionConflict("ARTIFACT_READ_UTF8_INVALID", "Published artifact bytes are not valid UTF-8 text") from None
    if offset > len(text):
        raise ExecutionConflict("ARTIFACT_READ_OFFSET_INVALID", "offset exceeds the artifact's character count")
    content = text[offset:offset + limit]
    end = offset + len(content)
    return {
        "artifact_id": artifact.id, "room_id": artifact.room_id,
        "filename": artifact.filename, "sha256": artifact.sha256,
        "mime": artifact.mime, "size_bytes": len(raw),
        "producer_agent_id": artifact.produced_by_agent_id,
        "task_id": source.id, "reader_task_id": task.id,
        "execution_id": execution.id, "input_revision": execution.input_revision,
        "publication_event_id": publication_id, "view_scope": scope,
        "result_id": result.id if result else None,
        "result_version": result.version if result else None,
        "result_sha256": result.result_sha256 if result else None,
        "accepted": result is not None,
        "offset": offset, "limit": limit, "total_characters": len(text),
        "next_offset": end if end < len(text) else None, "content": content,
    }
