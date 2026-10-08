"""Tasks an agent promotes from the request it is answering (#806).

Outside project executions, an agent answers a room with no task. When the
request needs real work, the agent calls ``claim_current_request``: the
server turns the message that started the turn into a task the agent owns,
already ``in_progress``, and binds it to the turn. A question about that
task (``request_task_input``) is posted in the task's source thread and
blocks the task; the first human reply in that thread answers it, claims the
task again and wakes the same agent with the answer.

The turn proof decides everything the model could get wrong: which message
becomes the task, who owns it and which task a question belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import AgentTurn, Message, Participant, Room, Task
from anygarden.db.task_input_request_models import TaskInputRequest
from anygarden.messages.service import append_message, inject_task_assignment_message
from anygarden.task_service import TaskMutationConflict, claim_task_cas

TITLE_LIMIT = 120
QUESTION_LIMIT = 20000
ANSWER_LIMIT = 100000
WAITING_ERROR = "사용자에게 추가 정보를 요청했습니다."


class GeneralTaskConflict(ValueError):
    def __init__(self, code: str, detail: str, **extra: str | None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.extra = extra

    def __str__(self) -> str:
        suffix = "".join(f"; {key}={value}" for key, value in self.extra.items())
        return f"{self.code}: {self.detail}{suffix}"


@dataclass
class ThreadAnswer:
    """A human thread reply that answered a pending task question."""

    request: TaskInputRequest
    requester_participant_id: str
    messages: list[Message]


def _now() -> datetime:
    return datetime.now(UTC)


def is_system_source(message: Message) -> bool:
    """A server-authored notice can never become a task."""

    metadata = message.extra_metadata or {}
    if metadata.get("system_origin") is not None:
        return True
    denied_keys = {
        "task_assignment",
        "room_query",
        "room_query_result",
        "room_query_forward",
        "routing_request_id",
    }
    return any(key in metadata for key in denied_keys)


def _title(content: str) -> str:
    line = next((part.strip() for part in content.splitlines() if part.strip()), "")
    if len(line) > TITLE_LIMIT:
        line = line[: TITLE_LIMIT - 1].rstrip() + "…"
    return line or "요청 작업"


def _text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GeneralTaskConflict("INVALID_ARGUMENT", f"{name} is required")
    if len(value) > limit:
        raise GeneralTaskConflict("INVALID_ARGUMENT", f"{name} is longer than {limit} characters")
    return value.strip()


async def _authorized_turn(db: AsyncSession, *, agent_id: str, proof) -> AgentTurn:
    from anygarden.project_executions.service import authorize_turn

    if proof is None:
        raise GeneralTaskConflict(
            "TURN_PROOF_REQUIRED", "This tool needs the current delivered turn lease",
        )
    return await authorize_turn(db, agent_id=agent_id, proof=proof)


async def promote_turn_request(
    db: AsyncSession, *, agent_id: str, proof
) -> tuple[Task, bool]:
    """Turn the message this turn answers into a task the agent owns.

    Returns the task and whether this call created it. Calling it again in
    the same turn, or in the turn that resumes the task, returns the same
    task.
    """

    from anygarden.turns.service import _is_operating_lead

    turn = await _authorized_turn(db, agent_id=agent_id, proof=proof)
    if turn.execution_id is not None:
        raise GeneralTaskConflict(
            "CLAIM_EXECUTION_TURN", "This turn already works on a project execution task",
        )
    if turn.task_id is not None:
        bound = await db.get(Task, turn.task_id)
        if (
            bound is not None
            and bound.execution_id is None
            and bound.assignee_participant_id == turn.target_participant_id
        ):
            return bound, False
        raise GeneralTaskConflict(
            "CLAIM_TURN_HAS_TASK", "This turn already belongs to another task",
            existing_task_id=turn.task_id,
        )
    if await _is_operating_lead(db, turn):
        raise GeneralTaskConflict(
            "CLAIM_OPERATING_ROOM",
            "Operating-room work is delegated with begin_project_execution",
        )

    source = await db.get(Message, turn.trigger_message_id)
    author = await db.get(Participant, source.participant_id) if source and source.participant_id else None
    if (
        source is None
        or source.room_id != turn.room_id
        or source.root_message_id is not None
        or author is None
        or author.agent_id is not None
        or is_system_source(source)
    ):
        raise GeneralTaskConflict(
            "CLAIM_SOURCE_INVALID",
            "Only a top-level human message that started this turn can become a task",
        )

    existing = await db.scalar(select(Task).where(Task.source_message_id == source.id))
    if existing is not None:
        return _bind_existing(turn, existing), False

    now = _now()
    task = Task(
        room_id=turn.room_id,
        source_message_id=source.id,
        title=_title(source.content),
        spec=source.content,
        status="in_progress",
        assignee_participant_id=turn.target_participant_id,
        assigned_at=now,
        started_at=now,
    )
    try:
        async with db.begin_nested():
            db.add(task)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(select(Task).where(Task.source_message_id == source.id))
        if existing is None:
            raise
        return _bind_existing(turn, existing), False
    turn.task_id = task.id
    await db.flush()
    return task, True


def _bind_existing(turn: AgentTurn, task: Task) -> Task:
    if task.execution_id is None and task.assignee_participant_id == turn.target_participant_id:
        turn.task_id = task.id
        return task
    raise GeneralTaskConflict(
        "TASK_SOURCE_ALREADY_LINKED", "This request is already another task",
        existing_task_id=task.id,
    )


async def request_input(
    db: AsyncSession, *, agent_id: str, proof, question_key: object, question: object,
) -> tuple[TaskInputRequest, list[Message]]:
    """Block the turn's task on a question posted in its source thread."""

    question_key = _text(question_key, "question_key", 160)
    question = _text(question, "question", QUESTION_LIMIT)
    turn = await _authorized_turn(db, agent_id=agent_id, proof=proof)
    task = await db.get(Task, turn.task_id) if turn.task_id else None
    if (
        task is None
        or task.execution_id is not None
        or task.source_message_id is None
        or task.assignee_participant_id != turn.target_participant_id
    ):
        raise GeneralTaskConflict(
            "REQUEST_TASK_NOT_CURRENT",
            "Call claim_current_request first; questions belong to the task of this turn",
        )
    existing = await db.scalar(select(TaskInputRequest).where(
        TaskInputRequest.task_id == task.id, TaskInputRequest.question_key == question_key,
    ))
    if existing is not None:
        if existing.question != question:
            raise GeneralTaskConflict(
                "REQUEST_KEY_REUSED", "This question key already has different text",
            )
        return existing, []
    pending = await db.scalar(select(TaskInputRequest.id).where(
        TaskInputRequest.task_id == task.id, TaskInputRequest.status == "pending",
    ))
    if pending is not None:
        raise GeneralTaskConflict(
            "REQUEST_PENDING_EXISTS", "This task already waits for an answer; end this turn",
        )
    newer = await newer_turn_for_task(db, task_id=task.id, turn=turn)
    if newer is not None:
        raise GeneralTaskConflict(
            "TASK_TURN_SUPERSEDED",
            "A newer turn now owns this task; end this turn without changing the task",
        )

    changed = await db.scalar(
        update(Task)
        .where(
            Task.id == task.id,
            Task.status == "in_progress",
            Task.assignee_participant_id == turn.target_participant_id,
        )
        .values(status="blocked", error=WAITING_ERROR)
        .returning(Task.id)
        .execution_options(synchronize_session="fetch")
    )
    if changed is None:
        raise GeneralTaskConflict(
            "REQUEST_TASK_NOT_CLAIMED", "Only an in-progress task can wait for an answer",
        )
    request = TaskInputRequest(
        task_id=task.id, room_id=task.room_id, question_key=question_key,
        question=question, requester_agent_id=agent_id,
        requester_participant_id=turn.target_participant_id,
        turn_request_id=turn.request_id,
    )
    db.add(request)
    await db.flush()
    message = await append_message(
        db, task.room_id, None,
        f"추가 정보가 필요합니다 · {task.title}\n\n{question}\n\n"
        "이 스레드에 남기는 다음 답글이 답변으로 전달되고, 같은 작업이 이어집니다.",
        {
            "system_origin": "task_input_request",
            "ingest_only": True,
            "task_input_request": {
                "id": request.id, "task_id": task.id,
                "question_key": question_key, "status": "pending",
            },
        },
        thread_root_id=task.source_message_id,
    )
    request.question_message_id = message.id
    await db.flush()
    return request, [message]


async def answer_from_thread(
    db: AsyncSession, *, reply: Message, user_id: str,
) -> ThreadAnswer | None:
    """Answer the pending question of the task whose thread *reply* is in.

    Returns ``None`` when the reply is ordinary thread conversation: no
    question is pending there, or the task stopped waiting for it.
    """

    root_id = reply.root_message_id
    if root_id is None:
        return None
    request = await db.scalar(
        select(TaskInputRequest)
        .join(Task, Task.id == TaskInputRequest.task_id)
        .where(
            Task.source_message_id == root_id,
            Task.room_id == reply.room_id,
            TaskInputRequest.status == "pending",
        )
        .with_for_update()
    )
    if request is None:
        return None
    task = await db.scalar(
        select(Task).where(Task.id == request.task_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    participant = (
        await db.get(Participant, request.requester_participant_id)
        if request.requester_participant_id else None
    )
    room = await db.get(Room, reply.room_id)
    if (
        task is None
        or task.status != "blocked"
        or task.assignee_participant_id != request.requester_participant_id
        or participant is None
        or participant.agent_id != request.requester_agent_id
        or room is None
        or room.archived_at is not None
    ):
        return None

    answer = reply.content[:ANSWER_LIMIT]
    try:
        async with db.begin_nested():
            request.status = "answered"
            request.answer = answer
            request.answer_message_id = reply.id
            request.answered_by_user_id = user_id
            request.answered_at = _now()
            task.spec = (task.spec or "") + (
                f"\n\n**USER ANSWER · question {request.question_key}**"
                f"\nQuestion: {request.question}\nAnswer: {answer}"
            )
            task.status = "todo"
            task.error = None
            await db.flush()
            task = await claim_task_cas(
                db, task_id=task.id, room_id=task.room_id, participant_id=participant.id,
            )
            resume = await inject_task_assignment_message(
                db, room=room, task=task, sender_participant_id=None, event="reassigned",
            )
            request.resume_message_id = resume.id
            await db.flush()
    except TaskMutationConflict:
        # Claim lost (e.g. a dependency reopened): the reply stays ordinary
        # conversation and the question remains pending.
        await db.refresh(request)
        return None
    return ThreadAnswer(
        request=request,
        requester_participant_id=participant.id,
        messages=[resume],
    )


async def newer_turn_for_task(db: AsyncSession, *, task_id: str, turn: AgentTurn) -> str | None:
    """Return a later turn of the same assignee that now owns *task_id*."""

    return await db.scalar(select(AgentTurn.request_id).where(
        AgentTurn.task_id == task_id,
        AgentTurn.target_participant_id == turn.target_participant_id,
        AgentTurn.request_id != turn.request_id,
        AgentTurn.created_at > turn.created_at,
    ).limit(1))
