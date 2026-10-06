"""Permission-scoped, stable read projection of project work and human requests."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from anygarden.auth.dependencies import Identity
from anygarden.db.execution_approval_models import ExecutionApproval
from anygarden.db.execution_request_models import ExecutionRequest
from anygarden.db.models import (
    ExecutionMutation,
    Participant,
    Project,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    RoomArtifact,
    Task,
    TaskResult,
)
from anygarden.project_executions.authorization import disposition
from anygarden.rooms.authorization import (
    Capability,
    accessible_room_ids,
    require_capability,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _disposition(execution: ProjectExecution, input_revision: int) -> str:
    return disposition(execution, input_revision)[1]


async def list_inbox(db: AsyncSession, *, identity: Identity, targets: dict) -> dict:
    """Project from current rows; redelivery cannot append another notification.

    Operating-room TASK_READ authorizes its execution ledger, including child
    tasks as in the existing room task API. Every referenced room/result/file
    must additionally belong to that same project.
    """
    from anygarden.api.v1.tasks import _participant_display_name
    from anygarden.project_executions.approvals import (
        approval_is_current,
        approval_payload,
    )
    from anygarden.project_executions.recovery import (
        public_reason_code,
        public_task_error,
        task_recovery_payload,
    )
    from anygarden.project_executions.requests import request_payload
    from anygarden.project_executions.service import ACTIVE_EXECUTION_STATES

    room_ids = await accessible_room_ids(db, identity=identity, scope="project.inbox")
    if not room_ids:
        return {"items": [], "projects": []}
    executions = (await db.execute(
        select(ProjectExecution, Project, Room)
        .join(Project, Project.id == ProjectExecution.project_id)
        .join(Room, and_(Room.id == ProjectExecution.operating_room_id, Room.project_id == Project.id))
        .where(Room.id.in_(room_ids))
    )).all()
    by_execution = {execution.id: (execution, project, room) for execution, project, room in executions}
    if not by_execution:
        return {"items": [], "projects": []}
    mutations = (await db.scalars(select(ExecutionMutation)
        .where(ExecutionMutation.execution_id.in_(by_execution))
        .order_by(ExecutionMutation.requested_at.desc(), ExecutionMutation.id))).all()
    operation_by_execution = {}
    for mutation in mutations:
        operation_by_execution.setdefault(mutation.execution_id,
            mutation.action if mutation.action in {"revise", "cancel", "deadline"} else None)
    access_by_room = {}
    for _, _, room in executions:
        if room.id not in access_by_room:
            access_by_room[room.id] = await require_capability(
                db, room_id=room.id, identity=identity, capability=Capability.TASK_READ,
            )

    def may_act(execution: ProjectExecution) -> bool:
        access = access_by_room[execution.operating_room_id]
        return identity.kind == "user" and not access.is_archived and (
            access.is_global_admin or access.effective_role in {"member", "admin", "owner"}
        )

    def base(kind: str, record_id: str, execution: ProjectExecution, task: Task, room: Room) -> dict:
        _, project, operating_room = by_execution[execution.id]
        return {
            "id": f"{kind}:{record_id}", "type": kind, "project_id": project.id,
            "project_name": project.name, "execution_id": execution.id,
            "execution_operation_action": operation_by_execution.get(execution.id),
            "execution_error": public_reason_code(execution.error),
            "operating_room_id": operating_room.id, "operating_room_name": operating_room.name,
            "task_id": task.id, "task_title": task.title, "task_room_id": room.id,
            "task_room_name": room.name, "source_message_id": execution.source_message_id,
            "task_href": f"/inbox?item=task%3A{task.id}",
            "input_revision": task.input_revision,
            "disposition": _disposition(execution, task.input_revision),
            "is_current": disposition(execution, task.input_revision)[0],
            "source_href": f"/rooms/{operating_room.id}?message={execution.source_message_id}" if execution.source_message_id else None,
        }

    task_rows = (await db.execute(
        select(Task, Room)
        .join(ProjectExecution, ProjectExecution.id == Task.execution_id)
        .join(Room, and_(Room.id == Task.room_id, Room.project_id == ProjectExecution.project_id))
        .where(Task.execution_id.in_(by_execution))
    )).all()
    tasks = {task.id: (task, room) for task, room in task_rows}
    # Select the immutable accepted version, rather than exposing a stale attempt.
    results = (await db.scalars(
        select(TaskResult).join(Task, and_(
            Task.id == TaskResult.task_id, Task.execution_id == TaskResult.execution_id,
            Task.result_version == TaskResult.version, Task.input_revision == TaskResult.input_revision,
        )).where(Task.id.in_(tasks))
    )).all() if tasks else []
    result_by_task = {result.task_id: result for result in results}
    events = (await db.scalars(
        select(ProjectExecutionEvent)
        .where(ProjectExecutionEvent.execution_id.in_(by_execution))
        .order_by(ProjectExecutionEvent.created_at.desc(), ProjectExecutionEvent.id.desc())
    )).all()
    event_by_task = {}
    artifact_ids = set()
    for event in events:
        if event.task_id in tasks and tasks[event.task_id][0].execution_id == event.execution_id:
            event_by_task.setdefault(event.task_id, event)
    for result in results:
        for reference in result.artifacts or []:
            if isinstance(reference, dict) and reference.get("artifact_id"):
                artifact_ids.add(reference["artifact_id"])
    artifact_rows = (await db.execute(
        select(RoomArtifact, Room).join(Room, Room.id == RoomArtifact.room_id)
        .where(RoomArtifact.id.in_(artifact_ids))
    )).all() if artifact_ids else []
    artifacts = {artifact.id: (artifact, room) for artifact, room in artifact_rows}
    items = []
    for task, room in task_rows:
        if task.is_silent:
            continue
        execution, _, _ = by_execution[task.execution_id]
        result = result_by_task.get(task.id)
        event = event_by_task.get(task.id)
        participant = await db.get(Participant, task.assignee_participant_id) if task.assignee_participant_id else None
        actor = await _participant_display_name(db, participant if participant and participant.room_id == task.room_id else None)
        files = []
        references = sorted(result.artifacts if result else [], key=lambda reference: isinstance(reference, dict) and reference.get("room_id") != execution.operating_room_id)
        seen_files = set()
        for reference in references:
            stored = artifacts.get(reference.get("artifact_id")) if isinstance(reference, dict) else None
            if not stored:
                continue
            artifact, artifact_room = stored
            # Download only a room that this identity can read; operating-room
            # publication ensures the usual ops-only member still gets its file.
            if artifact_room.project_id != execution.project_id or artifact_room.id not in room_ids:
                continue
            if reference.get("room_id") != artifact.room_id or reference.get("sha256") != artifact.sha256:
                continue
            file_key = (artifact.filename, artifact.sha256)
            if file_key in seen_files:
                continue
            seen_files.add(file_key)
            files.append({"id": artifact.id, "filename": artifact.filename, "sha256": artifact.sha256,
                          "url": f"/api/v1/rooms/{artifact.room_id}/artifacts/{artifact.id}"})
        item = base("task", task.id, execution, task, room)
        recovery = await task_recovery_payload(db, task, execution=execution,
            access=access_by_room[execution.operating_room_id])
        recovery_needs_action = bool(recovery and recovery["state"] in {"action_required", "failed"}
            and execution.status in ACTIVE_EXECUTION_STATES and task.input_revision == execution.input_revision)
        item.update({
            "status": task.status, "needs_action": recovery_needs_action,
            "current_action": "retry" if recovery and recovery["can_retry"] else "read" if recovery_needs_action else "none",
            "created_at": _iso(task.created_at),
            "updated_at": _iso(max(filter(None, [task.created_at, task.started_at, task.finished_at, event.created_at if event else None, result.created_at if result else None]))),
            "latest_event_id": event.id if event else None,
            "latest_event_type": event.event_type if event else None,
            "recovery": recovery,
            "task": {"id": task.id, "title": task.title, "status": task.status, "error": public_task_error(task.status, task.error),
                     "recovery": recovery,
                     "result_version": result.version if result else task.result_version,
                     "result_markdown": result.result_markdown if result else None,
                     "result_sha256": result.result_sha256 if result else None,
                     "input_revision": task.input_revision,
                     "disposition": _disposition(execution, task.input_revision),
                     "is_current": disposition(execution, task.input_revision)[0],
                     "assignee_display_name": actor, "artifacts": files},
        })
        items.append(item)

    requests = (await db.scalars(select(ExecutionRequest).where(
        ExecutionRequest.execution_id.in_(by_execution),
    ))).all()
    now = datetime.now(UTC)
    for row in requests:
        pair = tasks.get(row.task_id)
        execution, _, _ = by_execution[row.execution_id]
        if not pair or row.operating_room_id != execution.operating_room_id:
            continue
        task, room = pair
        if row.task_room_id != room.id or task.execution_id != execution.id:
            continue
        current = (execution.status in ACTIVE_EXECUTION_STATES and execution.input_revision == row.input_revision
                   and task.input_revision == row.input_revision and row.requester_participant_id is not None
                   and task.assignee_participant_id == row.requester_participant_id and room.archived_at is None
                   and (execution.deadline_at is None or execution.deadline_at > now))
        payload = request_payload(row)
        participant = await db.get(Participant, row.requester_participant_id) if row.requester_participant_id else None
        payload.update({"assignee_display_name": await _participant_display_name(db, participant if participant and participant.room_id == room.id else None),
                        "execution_operation_action": operation_by_execution.get(execution.id),
                        "execution_source_message_id": row.source_message_id, "is_current": current,
                        "can_answer": row.status == "pending" and current and task.status == "blocked" and may_act(execution),
                        "disposition": _disposition(execution, row.input_revision)})
        item = base("question", row.id, execution, task, room)
        item.update({"status": row.status, "needs_action": row.status == "pending" and current,
                     "input_revision": row.input_revision, "is_current": current,
                     "disposition": _disposition(execution, row.input_revision),
                     "current_action": "answer" if payload["can_answer"] else "read",
                     "created_at": _iso(row.created_at), "updated_at": _iso(row.answered_at or row.created_at), "question": payload})
        items.append(item)

    source_task, source_room, artifact_room = aliased(Task), aliased(Room), aliased(Room)
    approvals = (await db.scalars(
        select(ExecutionApproval)
        .join(ProjectExecution, ProjectExecution.id == ExecutionApproval.execution_id)
        .join(source_task, and_(source_task.id == ExecutionApproval.source_task_id, source_task.execution_id == ProjectExecution.id))
        .join(TaskResult, and_(TaskResult.id == ExecutionApproval.source_result_id, TaskResult.task_id == source_task.id, TaskResult.execution_id == ProjectExecution.id))
        .join(source_room, and_(source_room.id == source_task.room_id, source_room.project_id == ProjectExecution.project_id))
        .join(RoomArtifact, and_(RoomArtifact.id == ExecutionApproval.artifact_id, RoomArtifact.room_id == ExecutionApproval.artifact_room_id))
        .join(artifact_room, and_(artifact_room.id == RoomArtifact.room_id, artifact_room.project_id == ProjectExecution.project_id))
        .where(ProjectExecution.id.in_(by_execution))
    )).all()
    for row in approvals:
        pair = tasks.get(row.task_id)
        execution, _, _ = by_execution[row.execution_id]
        if not pair or row.operating_room_id != execution.operating_room_id:
            continue
        task, room = pair
        if row.task_room_id != room.id or task.execution_id != execution.id:
            continue
        payload = approval_payload(row)
        payload["artifact_accessible"] = row.artifact_room_id in room_ids
        if not payload["artifact_accessible"]:
            # Publication stores an identical operating-room copy. Keep the
            # decision bound to the original immutable artifact, but give an
            # ops-only reader a permitted download of the exact same bytes.
            mirror = await db.scalar(select(RoomArtifact).where(
                RoomArtifact.room_id == execution.operating_room_id,
                RoomArtifact.sha256 == row.artifact_sha256,
                RoomArtifact.filename == payload["artifact_filename"],
            ))
            payload["artifact_url"] = f"/api/v1/rooms/{mirror.room_id}/artifacts/{mirror.id}" if mirror else ""
            payload["artifact_accessible"] = mirror is not None
        current = await approval_is_current(db, row, targets=targets)
        participant = await db.get(Participant, row.requester_participant_id) if row.requester_participant_id else None
        source = tasks.get(row.source_task_id)
        payload.update({"assignee_display_name": await _participant_display_name(db, participant if participant and participant.room_id == room.id else None),
                        "execution_operation_action": operation_by_execution.get(execution.id),
                        "task_room_name": room.name, "source_task_title": source[0].title if source else None,
                        "source_task_room_id": source[1].id if source else None, "source_task_room_name": source[1].name if source else None,
                        "is_current": current, "can_decide": current and row.status == "pending" and task.status == "blocked" and may_act(execution),
                        "disposition": _disposition(execution, row.input_revision),
                        "revoked": row.status in {"pending", "approved"} and not current})
        item = base("approval", row.id, execution, task, room)
        item.update({"status": row.status, "needs_action": row.status == "pending" and current,
                     "input_revision": row.input_revision, "is_current": current,
                     "disposition": _disposition(execution, row.input_revision),
                     "current_action": "decide" if payload["can_decide"] else "read",
                     "created_at": _iso(row.created_at), "updated_at": _iso(row.finished_at or row.executed_at or row.decided_at or row.created_at), "approval": payload})
        items.append(item)
    # Identity is the source row/task, never delivery count or event timestamp.
    unique = {item["id"]: item for item in items}
    ordered = sorted(unique.values(), key=lambda item: (item["needs_action"], item["updated_at"], item["id"]), reverse=True)
    projects = {project.id: {"id": project.id, "name": project.name} for _, project, _ in executions}
    return {"items": ordered, "projects": sorted(projects.values(), key=lambda project: (project["name"], project["id"]))}
