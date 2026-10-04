"""Actual text artifacts, bound to a leased execution task and its operating room."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from anygarden.db.models import (
    AgentTurnAttempt,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    RoomArtifact,
    Task,
)
from anygarden.project_executions.result_artifacts import publication_producer
from anygarden.project_executions.service import (
    ExecutionConflict,
    assert_task_workflow_ready,
    authorize_turn,
)
from anygarden.rooms.artifact_storage import save_bytes
from anygarden.rooms.shared_files import sanitize_storage_name


async def publish_artifact(
    db, *, agent_id: str, proof, task_id: str, filename: str, content: str,
    artifact_files_dir: Path, mime: str = "text/markdown",
) -> dict:
    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    task = await db.get(Task, task_id)
    execution = await db.get(ProjectExecution, task.execution_id) if task and task.execution_id else None
    if (
        task is None or execution is None or turn.task_id != task.id
        or task.assignee_participant_id != turn.target_participant_id
        or task.room_id != turn.room_id or task.status != "in_progress"
        or task.input_revision != execution.input_revision
        or execution.status not in {"planning", "running", "waiting_children"}
    ):
        raise ExecutionConflict("ARTIFACT_TASK_NOT_ACTIVE", "Artifact requires your current active execution task")
    await assert_task_workflow_ready(db, task=task, proof=proof, operation="artifact")
    attempt = await db.scalar(select(AgentTurnAttempt).where(
        AgentTurnAttempt.turn_id == proof.request_id,
        AgentTurnAttempt.attempt_number == proof.attempt,
    ))
    producer = publication_producer(agent_id=agent_id, proof=proof, attempt_id=attempt.id)
    from anygarden.db.execution_request_models import ExecutionRequest

    if await db.scalar(select(ExecutionRequest.id).where(
        ExecutionRequest.task_id == task.id,
        ExecutionRequest.input_revision == task.input_revision,
        ExecutionRequest.turn_request_id == proof.request_id,
        ExecutionRequest.resume_message_id.is_not(None),
    )):
        raise ExecutionConflict(
            "ARTIFACT_CONTEXT_SUPERSEDED",
            "The answer-bearing continuation must publish the artifact after a user question",
        )
    if mime not in {"text/plain", "text/markdown", "text/csv", "application/json", "text/html"}:
        raise ValueError("Project text artifact MIME is not supported")
    name = sanitize_storage_name(filename)
    raw = content.encode("utf-8")
    digest = sha256(raw).hexdigest()
    references = []
    for room_id in dict.fromkeys([execution.operating_room_id, task.room_id]):
        room = await db.get(Room, room_id)
        if room is None or room.project_id != execution.project_id or room.archived_at is not None:
            raise ExecutionConflict("ARTIFACT_SCOPE_REVOKED", "Artifact destination is no longer active in this project")
        artifact = await db.scalar(select(RoomArtifact).where(
            RoomArtifact.room_id == room_id, RoomArtifact.sha256 == digest,
        ))
        if artifact is None:
            try:
                async with db.begin_nested():
                    artifact = RoomArtifact(
                        room_id=room_id, produced_by_agent_id=agent_id,
                        filename=name, storage_path="", sha256=digest,
                        size_bytes=len(raw), mime=mime,
                    )
                    db.add(artifact)
                    await db.flush()
                    stored = save_bytes(
                        artifact_files_dir=artifact_files_dir, room_id=room_id,
                        file_id=artifact.id, data=raw, max_size_bytes=768 * 1024,
                    )
                    artifact.storage_path = stored.storage_path
                    await db.flush()
            except IntegrityError:
                artifact = await db.scalar(select(RoomArtifact).where(
                    RoomArtifact.room_id == room_id, RoomArtifact.sha256 == digest,
                ))
        if artifact is None:
            raise ExecutionConflict("ARTIFACT_STORE_FAILED", "Artifact could not be saved")
        references.append({
            "artifact_id": artifact.id, "room_id": room_id,
            "filename": artifact.filename, "sha256": artifact.sha256,
            "producer_agent_id": artifact.produced_by_agent_id,
            "task_id": task.id, "execution_id": execution.id,
            "input_revision": task.input_revision,
            "url": f"/api/v1/rooms/{room_id}/artifacts/{artifact.id}",
        })
    key = f"artifact:{execution.id}:{task.id}:{task.input_revision}:{attempt.id}:{digest}"
    if not await db.scalar(select(ProjectExecutionEvent.id).where(ProjectExecutionEvent.event_key == key)):
        try:
            async with db.begin_nested():
                db.add(ProjectExecutionEvent(
                    execution_id=execution.id, task_id=task.id, event_key=key,
                    event_type="artifact_published", details={"artifacts": references, "producer": producer},
                ))
                await db.flush()
        except IntegrityError:
            if not await db.scalar(select(ProjectExecutionEvent.id).where(ProjectExecutionEvent.event_key == key)):
                raise
    return {"execution_id": execution.id, "task_id": task.id, "artifacts": references}
