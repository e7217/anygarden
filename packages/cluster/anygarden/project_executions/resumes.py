"""Lead-driven resume of a stopped execution task (EXE-21).

When a worker blocks its task, the lead is woken once with ``TASK_BLOCKED``.
Re-delegating cannot move that task: the same ``delegation_key`` replays the
blocked task and a changed spec is a key conflict, while the sealed plan still
requires it. ``resume_blocked_task`` lets the lead put the *same* task back in
progress with an appended guidance addendum and one new bound assignee turn.

The same applies when the worker's engine call failed with a terminal receipt
proving the native process finished (``MODEL_EXECUTION_FAILED``, or a
non-automatic ``MODEL_TRANSIENT_FAILURE``) and the same-task retry gate is
open. The failed request and its attempts stay recorded; the new assignment
message moves ``Task.source_message_id``, so the old turn is superseded and can
no longer change the task. A new turn (rather than another attempt of the old
request, as the user's ``retry_task`` does) carries the guidance and does not
spend the old request's retry budget. Unknown native outcomes stay user-only.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnOutbox,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    Task,
    TaskBlocker,
)

RESUME_EVENT_TYPE = "task_resumed_by_lead"
MAX_LEAD_RESUMES_PER_TASK = 2
MAX_GUIDANCE_CHARS = 20000


async def lead_resumes(db: AsyncSession, *, execution_id: str, task_id: str,
                       input_revision: int) -> list[ProjectExecutionEvent]:
    """Recorded lead resumes of the task at this input revision, oldest first."""
    events = await db.scalars(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution_id,
        ProjectExecutionEvent.task_id == task_id,
        ProjectExecutionEvent.event_type == RESUME_EVENT_TYPE,
    ))
    return sorted((e for e in events if e.details.get("input_revision") == input_revision),
                  key=lambda e: e.details.get("resume_number", 0))


async def lead_resume_count(db: AsyncSession, *, execution_id: str, task_id: str,
                            input_revision: int) -> int:
    return len(await lead_resumes(db, execution_id=execution_id, task_id=task_id,
                                  input_revision=input_revision))


def _payload(task: Task, resume_count: int) -> dict:
    return {
        "execution_id": task.execution_id, "task_id": task.id, "room_id": task.room_id,
        "assignee_participant_id": task.assignee_participant_id, "status": task.status,
        "input_revision": task.input_revision, "resume_count": resume_count,
    }


async def resume_blocked_task(
    db: AsyncSession, *, agent_id: str, proof, execution_id: str, task_id: str,
    guidance: str,
) -> dict:
    from anygarden.messages.service import inject_task_assignment_message
    from anygarden.project_executions import service
    from anygarden.project_executions.approvals import blocking_for_task
    from anygarden.project_executions.recovery import (
        engine_failure_resume_reason,
        public_task_error,
    )
    from anygarden.project_executions.usage import admission_disposition
    from anygarden.task_service import TaskMutationConflict, claim_task_cas

    reject = service._reject
    execution, _turn = await service._root_turn(
        db, agent_id=agent_id, proof=proof, execution_id=execution_id,
    )
    service._active(execution)
    root = await db.get(Task, execution.root_task_id)
    if root is None or root.status != "in_progress":
        reject("EXECUTION_LEAD_REQUIRED", "The lead's root task must be in progress to resume work")
    await service.assert_task_workflow_ready(db, task=root, proof=proof, operation="delegate")
    guidance = guidance.strip() if isinstance(guidance, str) else ""
    if not guidance or len(guidance) > MAX_GUIDANCE_CHARS:
        reject("RESUME_INPUT_INVALID",
               f"Concrete guidance of at most {MAX_GUIDANCE_CHARS} characters is required")
    task = await db.scalar(select(Task).where(Task.id == task_id).with_for_update()
                           .execution_options(populate_existing=True))
    if (task is None or task.execution_id != execution.id or task.id == root.id
            or task.input_revision != execution.input_revision
            or not task.required_for_execution):
        reject("RESUME_TASK_INVALID",
               "Only a required delegated task of this execution's current input can be resumed")
    guidance_sha = hashlib.sha256(guidance.encode("utf-8")).hexdigest()
    resumes = await lead_resumes(db, execution_id=execution.id, task_id=task.id,
                                 input_revision=task.input_revision)
    count = len(resumes)

    if task.status not in {"blocked", "failed"}:
        # An identical replay of the last resume returns the resumed task.
        if resumes and resumes[-1].details.get("guidance_sha256") == guidance_sha:
            return _payload(task, count)
        reject("TASK_NOT_BLOCKED", f"Task is {task.status}; only a task its worker blocked "
               "or whose engine call failed can be resumed")
    if await db.scalar(select(TaskBlocker.task_id).where(TaskBlocker.task_id == task.id).limit(1)):
        reject("TASK_WAITING_FOR_PREREQUISITES",
               "Task is waiting for its prerequisites and resumes automatically when they finish")
    if (await service._pending_requests(db, task)
            or await blocking_for_task(db, task_id=task.id, input_revision=task.input_revision)):
        reject("TASK_WAITING_FOR_USER",
               "Task is waiting for a user answer or approval, not for the lead")
    public_error = public_task_error(task.status, task.error)
    engine_reason = None
    if not (task.status == "blocked" and public_error == "TASK_BLOCKED"):
        engine_reason = await engine_failure_resume_reason(db, task=task, execution=execution)
        if engine_reason is None:
            if public_error == "PROCESS_OUTCOME_UNKNOWN":
                reject("TASK_OUTCOME_UNKNOWN",
                       "The worker's engine outcome is unknown (PROCESS_OUTCOME_UNKNOWN); it may "
                       "still be running, so only a user can retry once it is safely stopped")
            reject("TASK_NOT_BLOCKED",
                   f"Task stopped with {public_error or 'no reason'}; only a task its worker "
                   "blocked or whose engine call failed retryably can be resumed by the lead")
    if count >= MAX_LEAD_RESUMES_PER_TASK:
        reject("TASK_RESUME_LIMIT",
               f"This task was already resumed {count} times; ask the user or stop instead")

    participant = await db.get(Participant, task.assignee_participant_id)
    agent = await db.get(Agent, participant.agent_id) if participant and participant.agent_id else None
    room = await db.get(Room, task.room_id)
    if (participant is None or participant.room_id != task.room_id or agent is None
            or agent.desired_state != "running" or room is None or room.archived_at is not None):
        reject("RESUME_ASSIGNEE_UNAVAILABLE", "The task's assignee is no longer an active agent")
    admission = await admission_disposition(db, execution=execution, phase="intent")
    if not admission.allowed:
        reason = admission.reason_code or "EXECUTION_NATIVE_ADMISSION_DENIED"
        db.info.setdefault("project_execution_usage_denials", {})[execution.id] = reason
        reject(reason, "The execution cannot create more managed work within its limits")
    changed = await db.execute(update(ProjectExecution).where(
        ProjectExecution.id == execution.id,
        ProjectExecution.status.in_(service.ACTIVE_EXECUTION_STATES),
        ProjectExecution.input_revision == execution.input_revision,
        ProjectExecution.state_revision == execution.state_revision,
    ).values(state_revision=ProjectExecution.state_revision + 1, updated_at=service._now())
        .returning(ProjectExecution.id))
    if changed.scalar_one_or_none() is None:
        reject("EXECUTION_CHANGED", "Execution changed while resuming; read it again")

    number = count + 1
    why = ("Your previous attempt ended because the engine call failed "
           f"({engine_reason}); no result was accepted from it. The lead asks you to "
           "do this same task again" if engine_reason else
           "The lead reviewed why you stopped and asks you to continue this same task")
    # Keep the original assignment; the worker's raw block text stays out.
    task.spec = (task.spec or "") + (
        f"\n\n**LEAD GUIDANCE · resume {number} · input revision {task.input_revision}**\n"
        f"{why} with this guidance:\n{guidance}"
    )
    task.status = "todo"
    task.error = None
    task.finished_at = None
    await db.flush()
    # Fence delivery rows of the task's earlier requests so nothing queued for
    # a failed request can run beside the new turn (as retry_task does).
    old_turns = select(AgentTurn.request_id).where(
        AgentTurn.task_id == task.id, AgentTurn.execution_id == execution.id,
        AgentTurn.execution_input_revision == task.input_revision,
    )
    await db.execute(update(AgentTurnOutbox).where(
        AgentTurnOutbox.turn_id.in_(old_turns),
        AgentTurnOutbox.state.in_({"pending", "delivering"}),
    ).values(state="cancelled"))
    try:
        task = await claim_task_cas(db, task_id=task.id, room_id=task.room_id,
                                    participant_id=participant.id)
    except TaskMutationConflict as exc:
        reject("RESUME_TASK_CHANGED", exc.detail)
    message = await inject_task_assignment_message(
        db, room=room, task=task, sender_participant_id=None, event="reassigned",
    )
    service._queue_message(db, message)
    event = await service._event(
        db, execution,
        f"execution:{execution.id}:lead-resume:{task.id}:r{task.input_revision}:{number}:{guidance_sha[:16]}",
        RESUME_EVENT_TYPE, task_id=task.id,
        details={"input_revision": task.input_revision, "resume_number": number,
                 "guidance_sha256": guidance_sha, "resume_message_id": message.id,
                 "resumed_from": engine_reason or "TASK_BLOCKED"},
    )
    if event is None:
        reject("RESUME_CONFLICT", "A concurrent resume of this task was recorded")
    event.message_id = message.id
    await db.flush()
    return _payload(task, number)
