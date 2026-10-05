"""Plan independent QA early and freeze its exact target before assignment."""

from __future__ import annotations

from hashlib import sha256

from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    Agent,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    Task,
    TaskBlocker,
    TaskResult,
)
from anygarden.rooms.authorization import AGENT_EXECUTION_ROLES


async def validate_target_declaration(
    db: AsyncSession, *, execution: ProjectExecution, parent: Task,
    reviewer_agent_id: str, target_task_id: str | None,
    declared_version: int | None,
) -> Task:
    from anygarden.project_executions.service import _reject

    target = await db.get(Task, target_task_id) if target_task_id else None
    room = await db.get(Room, target.room_id) if target else None
    if (target is None or target.execution_id != execution.id
        or target.input_revision != execution.input_revision
        or room is None or room.project_id != execution.project_id
        or room.archived_at is not None
        or (declared_version is not None
            and (type(declared_version) is not int or declared_version < 1))):
        _reject("QA_TARGET_INVALID", "QA requires a target in this execution's current input revision")
    ancestor, visited = parent, set()
    while ancestor is not None:
        if ancestor.id == target.id or ancestor.id in visited:
            _reject("DELEGATION_DEPENDENCY_CYCLE", "QA cannot depend on its active parent or ancestor")
        visited.add(ancestor.id)
        ancestor = await db.get(Task, ancestor.parent_task_id) if ancestor.parent_task_id else None
    producer = await db.get(Participant, target.assignee_participant_id) if target.assignee_participant_id else None
    if (producer is None or producer.room_id != target.room_id
        or producer.agent_id is None or producer.agent_id == reviewer_agent_id):
        _reject("QA_TARGET_INVALID", "QA requires a different producer agent")
    if declared_version is not None:
        result = await db.scalar(select(TaskResult).where(
            TaskResult.task_id == target.id, TaskResult.execution_id == execution.id,
            TaskResult.input_revision == execution.input_revision,
            TaskResult.version == declared_version,
        ))
        if (target.status != "done" or target.result_version != declared_version
            or result is None or result.producer_agent_id == reviewer_agent_id
            or sha256(result.result_markdown.encode()).hexdigest() != result.result_sha256):
            _reject("QA_TARGET_INVALID", "QA requires another agent's completed result at its exact current version")
    elif target.status not in {"todo", "in_progress", "blocked", "done"}:
        _reject("QA_TARGET_INVALID", "Deferred QA cannot target failed or cancelled work")
    return target


async def bind_qa_target_for_claim(
    db: AsyncSession, *, task_id: str, room_id: str, participant_id: str,
) -> None:
    """Called inside the same savepoint as the claim; never commits itself."""
    from anygarden.project_executions.service import (
        ACTIVE_EXECUTION_STATES,
        _active,
        _event,
        _now,
        _reject,
        get_bound_task_execution_detail,
    )

    task = await db.get(Task, task_id, populate_existing=True, with_for_update=True)
    if task is None or task.role != "qa" or task.execution_id is None:
        return
    execution = await db.get(ProjectExecution, task.execution_id, populate_existing=True, with_for_update=True)
    reviewer = await db.get(Participant, participant_id, populate_existing=True, with_for_update=True)
    agent = await db.get(Agent, reviewer.agent_id) if reviewer and reviewer.agent_id else None
    if (execution is None or task.input_revision != execution.input_revision
        or task.room_id != room_id or task.status != "todo"
        or task.assignee_participant_id != participant_id
        or reviewer is None or reviewer.room_id != room_id
        or reviewer.role not in AGENT_EXECUTION_ROLES
        or agent is None or agent.desired_state != "running"):
        _reject("QA_TARGET_NOT_CLAIMABLE", "QA binding requires its current assigned agent and open task")
    _active(execution)
    if await db.scalar(select(TaskBlocker.task_id).where(TaskBlocker.task_id == task.id).limit(1)):
        _reject("QA_TARGET_WAITING", "QA must wait for every prerequisite before freezing its target")
    target = await db.get(Task, task.qa_target_task_id, populate_existing=True, with_for_update=True) if task.qa_target_task_id else None
    target_room = await db.get(Room, target.room_id) if target else None
    result = await db.scalar(select(TaskResult).where(
        TaskResult.task_id == task.qa_target_task_id,
        TaskResult.execution_id == execution.id,
        TaskResult.input_revision == execution.input_revision,
        TaskResult.version == target.result_version if target else TaskResult.version == -1,
    ).with_for_update())
    if (target is None or target.id == task.id or target.status != "done"
        or target.execution_id != execution.id or target.input_revision != execution.input_revision
        or target_room is None or target_room.project_id != execution.project_id
        or target_room.archived_at is not None or result is None
        or result.producer_agent_id == agent.id
        or (task.qa_target_result_version is not None and task.qa_target_result_version != result.version)
        or sha256(result.result_markdown.encode()).hexdigest() != result.result_sha256):
        _reject("QA_TARGET_INVALID", "QA target must be another agent's exact current accepted result")
    snapshots = list(task.dependency_results or [])
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            _reject("QA_TARGET_STALE", "QA prerequisite evidence is invalid")
        if snapshot.get("task_id"):
            # Refresh all inputs before the shared view verifies their frozen
            # versions; an earlier resolver read must not mask a newer result.
            await db.get(Task, snapshot["task_id"], populate_existing=True, with_for_update=True)
        if snapshot.get("task_id") == target.id and (
            snapshot.get("result_id") != result.id
            or snapshot.get("result_version") != result.version
            or snapshot.get("result_sha256") != result.result_sha256
        ):
            _reject("QA_TARGET_STALE", "The QA target changed after its prerequisite was accepted")
    frozen = {
        "task_id": target.id, "title": target.title, "room_id": target.room_id,
        "execution_id": execution.id, "input_revision": result.input_revision,
        "result_id": result.id, "result_version": result.version,
        "result_sha256": result.result_sha256, "result_markdown": result.result_markdown,
        "artifacts": result.artifacts,
        "finished_at": target.finished_at.isoformat() if target.finished_at else None,
        "result_created_at": result.created_at.isoformat(),
    }
    if not any(snapshot.get("task_id") == target.id for snapshot in snapshots):
        snapshots.append(frozen)
    key = f"execution:{execution.id}:qa_target_bound:{task.id}:r{task.input_revision}"
    previous = await db.scalar(select(ProjectExecutionEvent).where(ProjectExecutionEvent.event_key == key))
    if previous is not None:
        if (previous.details.get("result_id") != result.id
            or previous.details.get("result_sha256") != result.result_sha256
            or task.qa_target_result_version != result.version):
            _reject("QA_TARGET_BINDING_CONFLICT", "An assigned QA target cannot be rebound")
        await get_bound_task_execution_detail(db, execution_id=execution.id, task_id=task.id)
        return
    spec = (task.spec or "") + (
        f"\n\n**FROZEN QA TARGET**\nTask {target.id}; accepted result {result.id}; "
        f"version {result.version}; SHA-256 {result.result_sha256}; input revision {result.input_revision}.\n"
        f"{result.result_markdown}\n"
        "Read the actual artifact bodies with read_project_artifact before claiming a document review. "
        "Submit completed findings with verification={verdict: pass|fail, target_task_id: "
        f"'{target.id}', target_result_version: {result.version}}}. "
        "A failed quality check is done with verdict=fail; blocked means the review cannot be performed."
    )
    version_predicate = (Task.qa_target_result_version.is_(None) if task.qa_target_result_version is None
                         else Task.qa_target_result_version == task.qa_target_result_version)
    changed = await db.execute(update(Task).where(
        Task.id == task.id, Task.status == "todo", Task.input_revision == execution.input_revision,
        Task.assignee_participant_id == participant_id, version_predicate,
        ~exists(select(TaskBlocker.task_id).where(TaskBlocker.task_id == task.id)),
        exists(select(ProjectExecution.id).where(
            ProjectExecution.id == execution.id,
            ProjectExecution.input_revision == execution.input_revision,
            ProjectExecution.status.in_(ACTIVE_EXECUTION_STATES),
            or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > _now()),
        )),
    ).values(qa_target_result_version=result.version, dependency_results=snapshots, spec=spec).returning(Task.id))
    if changed.scalar_one_or_none() is None:
        _reject("QA_TARGET_BINDING_CONFLICT", "The QA task changed before its target could be frozen")
    await db.refresh(task)
    await get_bound_task_execution_detail(db, execution_id=execution.id, task_id=task.id)
    await _event(db, execution, key, "qa_target_bound", task_id=task.id, details={
        "target_task_id": target.id, "result_id": result.id, "result_version": result.version,
        "result_sha256": result.result_sha256, "input_revision": result.input_revision,
        "producer_agent_id": result.producer_agent_id, "reviewer_agent_id": agent.id,
    })
    await db.execute(update(ProjectExecution).where(ProjectExecution.id == execution.id).values(
        state_revision=ProjectExecution.state_revision + 1, updated_at=_now(),
    ))
