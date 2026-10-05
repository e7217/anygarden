"""Transactional information requests; one answer wakes only its original task."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.execution_request_models import ExecutionRequest
from anygarden.db.models import (
    Agent,
    AgentTurnAttempt,
    Message,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    Task,
    TaskBlocker,
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
        raise ExecutionConflict("REQUEST_TEXT_INVALID", f"{name} must contain 1 to {limit} characters")
    return value.strip()


def request_payload(request: ExecutionRequest) -> dict:
    return {
        "id": request.id,
        "execution_id": request.execution_id,
        "task_id": request.task_id,
        "operating_room_id": request.operating_room_id,
        "task_room_id": request.task_room_id,
        "task_title": request.task_title,
        "source_message_id": request.source_message_id,
        "input_revision": request.input_revision,
        "question_key": request.question_key,
        "question": request.question,
        "status": request.status,
        "requester_agent_id": request.requester_agent_id,
        "requester_participant_id": request.requester_participant_id,
        "question_message_id": request.question_message_id,
        "answer_message_id": request.answer_message_id,
        "resume_message_id": request.resume_message_id,
        "answer": request.answer,
        "answered_by_user_id": request.answered_by_user_id,
        "created_at": request.created_at.isoformat(),
        "answered_at": request.answered_at.isoformat() if request.answered_at else None,
    }


async def pending_for_task(
    db: AsyncSession, *, task_id: str, input_revision: int | None = None,
) -> list[ExecutionRequest]:
    stmt = select(ExecutionRequest).where(
        ExecutionRequest.task_id == task_id, ExecutionRequest.status == "pending",
    )
    if input_revision is not None:
        stmt = stmt.where(ExecutionRequest.input_revision == input_revision)
    return list(await db.scalars(stmt.order_by(ExecutionRequest.created_at, ExecutionRequest.id)))


async def list_pending_requests(db: AsyncSession, *, room_id: str) -> list[ExecutionRequest]:
    """Query only: the transport must authorize TASK_READ in this room first."""
    return list(await db.scalars(select(ExecutionRequest).where(
        ExecutionRequest.operating_room_id == room_id, ExecutionRequest.status == "pending",
    ).order_by(ExecutionRequest.created_at, ExecutionRequest.id)))


async def _fence_execution(db: AsyncSession, execution: ProjectExecution, revision: int) -> None:
    changed = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == execution.id,
        ProjectExecution.input_revision == revision,
        ProjectExecution.status.in_(ACTIVE_EXECUTION_STATES),
        or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > _now()),
        exists(select(Room.id).where(
            Room.id == ProjectExecution.operating_room_id,
            Room.project_id == ProjectExecution.project_id, Room.archived_at.is_(None),
        )),
    ).values(state_revision=ProjectExecution.state_revision + 1, updated_at=_now())
      .returning(ProjectExecution.id))
    if changed is None:
        raise ExecutionConflict("REQUEST_EXECUTION_CHANGED", "Execution is inactive or its input revision changed")


def _current_worker(task: Task, request: ExecutionRequest) -> bool:
    return (
        task.execution_id == request.execution_id
        and task.input_revision == request.input_revision
        and task.room_id == request.task_room_id
        and task.assignee_participant_id == request.requester_participant_id
        and request.requester_participant_id is not None
    )


async def _source_root(db: AsyncSession, execution: ProjectExecution) -> str:
    source = await db.get(Message, execution.source_message_id)
    if source is None or source.room_id != execution.operating_room_id:
        raise ExecutionConflict("REQUEST_SOURCE_INVALID", "Original operating-room request is unavailable")
    return source.root_message_id or source.id


def _queue(db: AsyncSession, *messages: Message) -> None:
    db.info.setdefault("project_execution_messages", []).extend(messages)


async def question(
    db: AsyncSession, *, agent_id: str, proof: TurnProof, task_id: str,
    question_key: str, question: str,
) -> ExecutionRequest:
    question_key = _text(question_key, "question_key", 160)
    question = _text(question, "question", 20000)
    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    task = await db.get(Task, task_id)
    if (task is None or turn.task_id != task.id or turn.room_id != task.room_id
        or task.assignee_participant_id != turn.target_participant_id or not task.execution_id):
        raise ExecutionConflict("REQUEST_TASK_NOT_CURRENT", "Question must belong to the task of this worker turn")
    execution = await db.get(ProjectExecution, task.execution_id)
    if execution is None or task.input_revision != execution.input_revision:
        raise ExecutionConflict("REQUEST_INPUT_REVISION_STALE", "Task input revision is no longer current")
    scope = (
        ExecutionRequest.execution_id == execution.id, ExecutionRequest.task_id == task.id,
        ExecutionRequest.input_revision == task.input_revision,
        ExecutionRequest.question_key == question_key,
    )
    existing = await db.scalar(select(ExecutionRequest).where(*scope))
    if existing is not None:
        if existing.question != question:
            raise ExecutionConflict("REQUEST_KEY_REUSED", "This question key already has different text")
        return existing
    from anygarden.project_executions.approvals import blocking_for_task
    from anygarden.project_executions.service import require_execution_operation

    require_execution_operation(execution, "request_input")
    if await blocking_for_task(db, task_id=task.id, input_revision=task.input_revision):
        raise ExecutionConflict("REQUEST_TASK_WAITING_FOR_APPROVAL", "Resolve the current action approval before asking another question")
    previous = await pending_for_task(db, task_id=task.id, input_revision=task.input_revision)
    if task.status != "in_progress" and not (
        task.status == "blocked" and any(r.turn_request_id == turn.request_id for r in previous)
    ):
        raise ExecutionConflict("REQUEST_TASK_NOT_CLAIMED", "Only the currently claimed worker can ask for input")
    task_room = await db.get(Room, task.room_id)
    if task_room is None or task_room.project_id != execution.project_id:
        raise ExecutionConflict("REQUEST_PROJECT_SCOPE_INVALID", "Task and execution must belong to the same project")
    attempt = await db.scalar(select(AgentTurnAttempt).where(
        AgentTurnAttempt.turn_id == turn.request_id, AgentTurnAttempt.attempt_number == proof.attempt,
    ))
    request = ExecutionRequest(
        execution_id=execution.id, task_id=task.id, operating_room_id=execution.operating_room_id,
        task_room_id=task.room_id, task_title=task.title, source_message_id=execution.source_message_id,
        input_revision=task.input_revision, question_key=question_key, question=question,
        requester_agent_id=agent_id, requester_participant_id=turn.target_participant_id,
        turn_request_id=turn.request_id, attempt_id=attempt.id,
    )
    try:
        async with db.begin_nested():
            db.add(request)
            await db.flush()
            await _fence_execution(db, execution, request.input_revision)
            resumed_from_turn = await db.scalar(select(ExecutionRequest.id).where(
                ExecutionRequest.turn_request_id == turn.request_id,
                ExecutionRequest.resume_message_id.is_not(None),
            ).limit(1))
            if resumed_from_turn is not None:
                raise ExecutionConflict(
                    "REQUEST_CONTEXT_SUPERSEDED",
                    "This worker turn predates the operating-room answer; use the resumed task turn",
                )
            changed = await db.scalar(update(Task).where(
                Task.id == task.id, Task.execution_id == execution.id,
                Task.input_revision == request.input_revision, Task.status == task.status,
                Task.assignee_participant_id == request.requester_participant_id,
                exists(select(Agent.id).where(Agent.id == agent_id, Agent.generation == proof.generation)),
                exists(select(Participant.id).where(
                    Participant.id == request.requester_participant_id,
                    Participant.agent_id == agent_id, Participant.room_id == task.room_id,
                    Participant.role.in_(AGENT_EXECUTION_ROLES),
                )),
                exists(select(Room.id).where(
                    Room.id == task.room_id, Room.project_id == execution.project_id,
                    Room.archived_at.is_(None),
                )),
            ).values(status="blocked", error="운영실에 추가 정보를 요청했습니다.").returning(Task.id))
            if changed is None:
                raise ExecutionConflict("REQUEST_TASK_CHANGED", "Task changed before the question was recorded")
            message = await append_message(
                db, execution.operating_room_id, None,
                f"추가 정보가 필요합니다 · {task.title}\n\n{question}\n\n"
                "이 질문에 답하면 같은 작업이 이어집니다. 다른 작업은 계속 진행합니다.",
                {"system_origin": "execution_request", "ingest_only": True,
                 "execution_request": {"id": request.id, "execution_id": execution.id,
                    "task_id": task.id, "input_revision": request.input_revision,
                    "question_key": question_key, "status": "pending"}},
                thread_root_id=await _source_root(db, execution),
            )
            request.question_message_id = message.id
            db.add(ProjectExecutionEvent(
                execution_id=execution.id, task_id=task.id,
                event_key=f"request:{request.id}:question", event_type="input_requested",
                details={"request_id": request.id, "input_revision": request.input_revision},
                message_id=message.id,
            ))
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(select(ExecutionRequest).where(*scope))
        if existing is None:
            raise
        if existing.question != question:
            raise ExecutionConflict("REQUEST_KEY_REUSED", "This question key already has different text") from None
        return existing
    _queue(db, message)
    return request


async def answer(
    db: AsyncSession, *, useridentity: Identity, request_id: str, answer: str,
) -> ExecutionRequest:
    answer = _text(answer, "answer", 100000)
    if useridentity.kind != "user":
        raise HTTPException(403, "Only an authorized user can answer an operating-room question")
    request = await db.get(ExecutionRequest, request_id)
    if request is None:
        raise HTTPException(404, "Execution request not found")
    access = await require_capability(
        db, room_id=request.operating_room_id, identity=useridentity, capability=Capability.MESSAGE_SEND,
    )
    execution = await db.get(ProjectExecution, request.execution_id)
    if (execution is None or execution.operating_room_id != request.operating_room_id
        or access.room.project_id != execution.project_id):
        raise HTTPException(404, "Execution request not found")
    if execution.input_revision != request.input_revision:
        raise ExecutionConflict("REQUEST_INPUT_REVISION_STALE", "Question belongs to an older input revision")
    if request.status == "answered":
        if request.answer == answer:
            return request
        raise ExecutionConflict("REQUEST_ALREADY_ANSWERED", "This question already has a different answer")
    messages = []
    async with db.begin_nested():
        await _fence_execution(db, execution, request.input_revision)
        request = await db.scalar(select(ExecutionRequest).where(ExecutionRequest.id == request_id)
                                  .with_for_update().execution_options(populate_existing=True))
        if request.status == "answered":
            if request.answer == answer:
                return request
            raise ExecutionConflict("REQUEST_ALREADY_ANSWERED", "This question already has a different answer")
        task = await db.scalar(select(Task).where(Task.id == request.task_id)
                              .with_for_update().execution_options(populate_existing=True))
        participant = await db.get(Participant, request.requester_participant_id)
        room = await db.get(Room, request.task_room_id)
        if (task is None or not _current_worker(task, request) or task.status != "blocked"
            or participant is None or participant.agent_id != request.requester_agent_id
            or participant.room_id != request.task_room_id or participant.role not in AGENT_EXECUTION_ROLES
            or room is None or room.archived_at is not None
            or room.project_id != execution.project_id):
            raise ExecutionConflict("REQUEST_TASK_CHANGED", "Original task is no longer waiting for this answer")
        request.status = "answered"
        request.answer = answer
        request.answered_by_user_id = useridentity.id
        request.answered_at = _now()
        task.spec = (task.spec or "") + (
            f"\n\n**OPERATING ROOM ANSWER · request {request.id} · input revision {request.input_revision}**"
            f"\nQuestion: {request.question}\nAnswer: {answer}"
        )
        message = await append_message(
            db, request.operating_room_id, access.participant.id if access.participant else None,
            f"추가 정보 답변 · {request.task_title}\n\n{answer}",
            {"ingest_only": True, "system_origin": "execution_request_answer",
             "execution_request": {"id": request.id, "execution_id": execution.id,
                "task_id": task.id, "input_revision": request.input_revision, "status": "answered"},
             "answered_by_user_id": useridentity.id},
            thread_root_id=await _source_root(db, execution),
        )
        request.answer_message_id = message.id
        messages.append(message)
        await db.flush()
        remaining = await pending_for_task(db, task_id=task.id, input_revision=request.input_revision)
        dependencies = await db.scalar(select(TaskBlocker.task_id).where(TaskBlocker.task_id == task.id).limit(1))
        from anygarden.project_executions.approvals import blocking_for_task

        approval_wait = await blocking_for_task(db, task_id=task.id, input_revision=request.input_revision)
        if not remaining and dependencies is None and not approval_wait:
            task.status = "todo"
            task.error = None
            await db.flush()
            try:
                task = await claim_task_cas(
                    db, task_id=task.id, room_id=task.room_id, participant_id=participant.id,
                )
            except TaskMutationConflict as exc:
                raise ExecutionConflict("REQUEST_TASK_CHANGED", exc.detail) from None
            resume = await inject_task_assignment_message(
                db, room=room, task=task, sender_participant_id=None, event="reassigned",
            )
            request.resume_message_id = resume.id
            messages.append(resume)
        db.add(ProjectExecutionEvent(
            execution_id=execution.id, task_id=task.id,
            event_key=f"request:{request.id}:answer", event_type="input_answered",
            details={"request_id": request.id, "input_revision": request.input_revision,
                     "resumed": request.resume_message_id is not None},
            message_id=message.id,
        ))
        await db.flush()
    _queue(db, *messages)
    return request
