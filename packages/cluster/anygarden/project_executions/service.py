"""Transactional project execution operations shared by MCP and HTTP.

Invocations identify an actual leased attempt; no operation guesses the latest
turn. Callers commit and broadcast queued messages after commit. Runtime shell,
network and token limits require the execution adapter's enforcement as well;
the delegation/deadline checks here do not claim to sandbox an engine.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    ExecutionInputRevision,
    Message,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    RoomArtifact,
    RoomSharedFile,
    Task,
    TaskBlocker,
    TaskResult,
)
from anygarden.messages.service import append_message, inject_task_assignment_message
from anygarden.project_executions.policy import (
    INTERNAL_TASK_ROLES,
    allows_operation,
    repair_round_limit,
    unsupported_policy,
)
from anygarden.rooms.authorization import AGENT_EXECUTION_ROLES
from anygarden.task_service import claim_task_cas, transition_task_status_cas
from anygarden.turns.service import ACTIVE_ATTEMPT_STATES, OPEN_TURN_STATES, create_turn

ACTIVE_EXECUTION_STATES = frozenset({"planning", "running", "waiting_children"})
log = logging.getLogger(__name__)
CHILD_HANDOFF_EVENT = "task_child_result_handoff"
CHILD_HANDOFF_ORIGIN = "project_execution_child_handoff"


@dataclass(frozen=True, slots=True)
class TurnProof:
    request_id: str
    attempt: int
    generation: int
    lease: str


class ExecutionConflict(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _reject(code: str, detail: str) -> None:
    raise ExecutionConflict(code, detail)


def _now() -> datetime:
    return datetime.now(UTC)


def _queue_message(db: AsyncSession, message: Message) -> None:
    db.info.setdefault("project_execution_messages", []).append(message)


def delegation_total_expression(execution_id: str):
    """The durable child ledger is authoritative across every input revision."""
    from anygarden.project_executions.qa_repairs import (
        logical_delegation_total_expression,
    )

    return logical_delegation_total_expression(execution_id)


async def authorize_turn(
    db: AsyncSession, *, agent_id: str, proof: TurnProof
) -> AgentTurn:
    """Fence an invocation to its current agent generation and live lease."""
    from anygarden.project_executions.usage import _lock_execution

    # The execution is also locked by limit/cancellation fencing. Acquire it
    # before its turn/attempt, using a nonlocking snapshot only to find scope.
    scope = (await db.execute(select(
        AgentTurn.execution_id, AgentTurn.execution_input_revision,
    ).where(AgentTurn.request_id == proof.request_id,
            AgentTurn.agent_id == agent_id))).first()
    if scope is None:
        _reject("STALE_TURN_PROOF", "A current request/attempt/generation/lease is required")
    if scope.execution_id is not None:
        await _lock_execution(db, scope.execution_id)
    turn = await db.get(
        AgentTurn, proof.request_id, populate_existing=True, with_for_update=True
    )
    if turn is None or (turn.execution_id, turn.execution_input_revision) != tuple(scope):
        _reject("STALE_TURN_PROOF", "Invocation scope changed while acquiring its fence")
    attempt = await db.scalar(
        select(AgentTurnAttempt)
        .where(
            AgentTurnAttempt.turn_id == proof.request_id,
            AgentTurnAttempt.attempt_number == proof.attempt,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    agent = await db.get(Agent, agent_id, populate_existing=True, with_for_update=True)
    if (
        turn is None
        or attempt is None
        or agent is None
        or agent.desired_state != "running"
        or turn.agent_id != agent_id
        or attempt.agent_id != agent_id
        or turn.state not in OPEN_TURN_STATES
        or turn.active_attempt != proof.attempt
        or attempt.state not in ACTIVE_ATTEMPT_STATES
        or attempt.generation != proof.generation
        or agent.generation != proof.generation
        or not proof.lease
        or not hmac.compare_digest(attempt.lease_token, proof.lease)
        or attempt.lease_expires_at is None
        or attempt.lease_expires_at <= _now()
    ):
        _reject(
            "STALE_TURN_PROOF", "A current request/attempt/generation/lease is required"
        )
    participant = await db.get(Participant, turn.target_participant_id)
    room = await db.get(Room, turn.room_id)
    if (
        participant is None
        or participant.agent_id != agent_id
        or participant.role not in AGENT_EXECUTION_ROLES
        or participant.room_id != turn.room_id
        or room is None
        or room.archived_at is not None
    ):
        _reject(
            "TURN_ROOM_ACCESS_REVOKED", "Turn participant or room is no longer active"
        )
    from anygarden.project_executions.authorization import validate_execution_turn

    allowed, code, _ = await validate_execution_turn(db, turn, lock=True)
    if not allowed:
        _reject(code or "EXECUTION_AUTHORIZATION_REVOKED", "The invocation's execution input is no longer authorized")
    return turn


async def _execution(db: AsyncSession, execution_id: str) -> ProjectExecution:
    execution = await db.get(ProjectExecution, execution_id)
    if execution is None:
        _reject("EXECUTION_NOT_FOUND", "Project execution not found")
    return execution


def _active(execution: ProjectExecution) -> None:
    if execution.status not in ACTIVE_EXECUTION_STATES:
        _reject("EXECUTION_NOT_ACTIVE", f"Execution is {execution.status}")
    if execution.deadline_at is not None and execution.deadline_at <= _now():
        _reject("EXECUTION_DEADLINE_REACHED", "Execution deadline has been reached")
    _validate_policy(execution.allowed_actions, execution.limits)


def _validate_policy(allowed_actions: list | None, limits: dict | None) -> None:
    if allowed_actions is not None and not isinstance(allowed_actions, list):
        _reject("INVALID_EXECUTION_POLICY", "allowed_actions must be an array")
    if limits is not None and not isinstance(limits, dict):
        _reject("INVALID_EXECUTION_POLICY", "limits must be an object")
    actions, limit_names = unsupported_policy(allowed_actions, limits)
    if actions:
        _reject(
            "UNSUPPORTED_EXECUTION_ACTION",
            f"The managed workflow cannot enforce these action declarations: {actions}",
        )
    if limit_names:
        _reject(
            "UNSUPPORTED_EXECUTION_LIMIT",
            f"The current execution adapter cannot enforce these limits: {limit_names}",
        )
    try:
        repair_round_limit(limits)
    except ValueError as exc:
        _reject("INVALID_EXECUTION_LIMIT", str(exc))
    for key in ("max_delegations", "max_depth", "max_native_invocations", "max_total_tokens"):
        if key in (limits or {}):
            value = limits[key]
            if type(value) is not int or not 1 <= value <= (1 << 63) - 1:
                _reject("INVALID_EXECUTION_LIMIT", f"{key} must be a positive int64")


def require_execution_operation(execution: ProjectExecution, operation: str) -> None:
    """Authorize a supported managed operation, never a native-engine grant."""
    _active(execution)
    if not allows_operation(execution.allowed_actions, operation):
        _reject(
            "EXECUTION_ACTION_NOT_ALLOWED",
            f"The execution does not allow managed operation {operation}",
        )


async def _root_turn(
    db: AsyncSession, *, agent_id: str, proof: TurnProof, execution_id: str
) -> tuple[ProjectExecution, AgentTurn]:
    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    execution = await _execution(db, execution_id)
    if (
        execution.lead_agent_id != agent_id
        or turn.room_id != execution.operating_room_id
        or turn.task_id != execution.root_task_id
    ):
        _reject(
            "EXECUTION_LEAD_REQUIRED",
            "Only this execution's bound lead turn can change the plan",
        )
    return execution, turn


async def _event(
    db: AsyncSession,
    execution: ProjectExecution,
    key: str,
    event_type: str,
    *,
    task_id: str | None = None,
    details: dict | None = None,
) -> ProjectExecutionEvent | None:
    if await db.scalar(
        select(ProjectExecutionEvent.id).where(ProjectExecutionEvent.event_key == key)
    ):
        return None
    event = ProjectExecutionEvent(
        execution_id=execution.id,
        task_id=task_id,
        event_key=key,
        event_type=event_type,
        details=details or {},
    )
    try:
        async with db.begin_nested():
            db.add(event)
            await db.flush()
    except IntegrityError:
        if not await db.scalar(
            select(ProjectExecutionEvent.id).where(
                ProjectExecutionEvent.event_key == key
            )
        ):
            raise
        return None
    return event


async def _input_snapshot(
    db: AsyncSession, room: Room, input_files: list[dict] | None
) -> list[dict]:
    snapshots = []
    for item in input_files or []:
        file_id = item.get("file_id") or item.get("id")
        row = await db.get(RoomSharedFile, file_id) if file_id else None
        if (
            row is None
            or row.room_id != room.id
            or item.get("room_id", room.id) != room.id
        ):
            _reject(
                "INPUT_FILE_SCOPE_INVALID",
                "Input files must be uploaded to the operating room",
            )
        if "content_base64" in item:
            try:
                raw = base64.b64decode(item["content_base64"], validate=True)
            except (ValueError, TypeError):
                _reject("INPUT_FILE_CONTENT_INVALID", "Input file bytes are invalid")
            contents = {"content_base64": item["content_base64"]}
        elif isinstance(item.get("content"), str):
            raw = item["content"].encode("utf-8")
            contents = {"content": item["content"]}
        else:
            _reject(
                "INPUT_FILE_CONTENT_REQUIRED",
                "Provide the actual input file content snapshot",
            )
        digest = hashlib.sha256(raw).hexdigest()
        if (
            digest != row.sha256
            or item.get("sha256", digest) != digest
            or len(raw) != row.size_bytes
        ):
            _reject(
                "INPUT_FILE_CHANGED",
                "Input content does not match the uploaded file hash and size",
            )
        snapshots.append(
            {
                "file_id": row.id,
                "room_id": room.id,
                "filename": row.filename,
                "sha256": digest,
                "size_bytes": len(raw),
                **contents,
            }
        )
    return snapshots


async def begin_execution(
    db: AsyncSession,
    *,
    agent_id: str,
    proof: TurnProof,
    objective: str | None = None,
    completion_criteria: list | None = None,
    allowed_actions: list | None = None,
    limits: dict | None = None,
    input_files: list[dict] | None = None,
    requires_qa: bool = False,
) -> ProjectExecution:
    from anygarden.project_executions.authorization import (
        bind_execution_turn,
        frozen_input_payload,
    )

    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    room = await db.get(Room, turn.room_id)
    if (
        room.project_id is None
        or room.is_dm
        or agent_id
        not in {
            room.orchestrator_agent_id,
            room.representative_agent_id,
        }
    ):
        _reject(
            "OPERATING_LEAD_REQUIRED",
            "A project room's configured lead must begin the execution",
        )
    if turn.task_id:
        bound_task = await db.get(Task, turn.task_id)
        if bound_task is not None and bound_task.execution_id:
            bound = await _execution(db, bound_task.execution_id)
            if bound.lead_agent_id != agent_id or bound.root_task_id != bound_task.id:
                _reject(
                    "EXECUTION_LEAD_REQUIRED",
                    "Only the bound operating lead can resume this execution",
                )
            _active(bound)
            await bind_execution_turn(db, turn=turn, task=bound_task)
            return bound
    existing = await db.scalar(
        select(ProjectExecution).where(
            ProjectExecution.source_message_id == turn.trigger_message_id
        )
    )
    if existing is not None:
        if existing.lead_agent_id != agent_id:
            _reject(
                "EXECUTION_SOURCE_CONFLICT",
                "This request already belongs to another execution",
            )
        if turn.task_id not in {None, existing.root_task_id}:
            _reject(
                "TURN_TASK_CONFLICT", "Current turn is already bound to another task"
            )
        existing_root = await db.get(Task, existing.root_task_id)
        if existing_root is None or existing_root.source_message_id != turn.trigger_message_id:
            _reject("EXECUTION_SOURCE_SUPERSEDED", "The original source cannot start a later input revision")
        turn.task_id = existing.root_task_id
        _active(existing)
        await bind_execution_turn(db, turn=turn, task=existing_root)
        return existing
    native_id = await db.scalar(select(AgentTurnAttempt.local_execution_id).where(
        AgentTurnAttempt.turn_id == turn.request_id,
        AgentTurnAttempt.attempt_number == proof.attempt,
    ))
    if native_id is None:
        _reject("NATIVE_TURN_CONTROL_REQUIRED", "Project execution requires an exact native-start permit and stop-capable runtime")
    source = await db.get(Message, turn.trigger_message_id)
    author = await db.get(Participant, source.participant_id) if source else None
    root = await db.get(Task, turn.task_id) if turn.task_id else None
    if source is None or source.room_id != room.id:
        _reject(
            "USER_SOURCE_REQUIRED",
            "Execution must be linked to its actual operating-room request",
        )
    # A scheduled Goal may be promoted without copying its goal_id to children.
    if (author is None or author.user_id is None) and (
        root is None
        or root.goal_id is None
        or root.source_message_id not in {None, source.id}
    ):
        _reject(
            "USER_SOURCE_REQUIRED",
            "Begin from a user request or a source-linked scheduled root task",
        )
    text = objective.strip() if objective else source.content.strip()
    if not text:
        _reject("OBJECTIVE_REQUIRED", "A concrete objective is required")
    _validate_policy(allowed_actions, limits)
    snapshots = await _input_snapshot(db, room, input_files)
    run_limits = dict(limits or {})
    for key in ("max_delegations", "max_depth"):
        value = run_limits.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            _reject("INVALID_EXECUTION_LIMIT", f"{key} must be a positive integer")
    deadline = None
    if run_limits.get("deadline_at"):
        try:
            deadline = datetime.fromisoformat(run_limits["deadline_at"])
        except (ValueError, TypeError):
            _reject("INVALID_EXECUTION_LIMIT", "deadline_at must be an ISO timestamp")
        if deadline.tzinfo is None or deadline <= _now():
            _reject(
                "INVALID_EXECUTION_LIMIT",
                "deadline_at must be a future timestamp with timezone",
            )
    if root is not None and (
        root.room_id != room.id
        or root.execution_id
        or root.assignee_participant_id != turn.target_participant_id
    ):
        _reject("TURN_TASK_CONFLICT", "Existing turn task is not an unbound lead root")
    if root is None:
        root = await db.scalar(select(Task).where(Task.source_message_id == source.id))
        if root is not None and (
            root.execution_id
            or root.assignee_participant_id != turn.target_participant_id
        ):
            _reject("TURN_TASK_CONFLICT", "Source request already has another task")
    if root is None:
        root = Task(
            room_id=room.id,
            source_message_id=source.id,
            title=text[:500],
            status="in_progress",
            assignee_participant_id=turn.target_participant_id,
            created_by=author.user_id if author else None,
            triggered_by="project_execution",
            assigned_at=_now(),
            started_at=_now(),
            spec=source.content,
        )
        db.add(root)
        await db.flush()
    else:
        if root.status not in {"todo", "in_progress"} or await db.scalar(
            select(TaskBlocker.task_id).where(TaskBlocker.task_id == root.id).limit(1)
        ):
            _reject(
                "ROOT_TASK_NOT_CLAIMABLE",
                "An existing source task must be open and have no unresolved prerequisites",
            )
        if root.status == "todo":
            root = await claim_task_cas(
                db,
                task_id=root.id,
                room_id=room.id,
                participant_id=turn.target_participant_id,
            )
    execution = ProjectExecution(
        project_id=room.project_id,
        operating_room_id=room.id,
        lead_agent_id=agent_id,
        owner_user_id=author.user_id if author else None,
        source_message_id=source.id,
        root_task_id=root.id,
        goal_id=root.goal_id,
        objective=text,
        completion_criteria=list(completion_criteria or []),
        allowed_actions=list(allowed_actions or []),
        limits=run_limits,
        requires_qa=requires_qa,
        deadline_at=deadline,
    )
    db.add(execution)
    await db.flush()
    root.execution_id = execution.id
    root.source_message_id = source.id
    root.input_revision = 1
    root.role = "orchestration"
    root.required_for_execution = True
    root.delegation_depth = 0
    root.status = "in_progress"
    root.started_at = root.started_at or _now()
    turn.task_id = root.id
    revision = ExecutionInputRevision(
            execution_id=execution.id,
            revision=1,
            objective=text,
            user_constraints=source.content,
            input_files=snapshots,
            completion_criteria=execution.completion_criteria,
            source_message_id=source.id,
            actor_user_id=execution.owner_user_id,
            root_task_id=root.id,
            change_scope="all",
        )
    frozen_input_payload(execution, revision)
    db.add(revision)
    await bind_execution_turn(db, turn=turn, task=root)
    await _event(
        db,
        execution,
        f"execution:{execution.id}:begun",
        "execution_begun",
        task_id=root.id,
        details={"request_id": turn.request_id, "input_revision": 1},
    )
    await db.flush()
    return execution


async def _descendant_room(
    db: AsyncSession,
    execution: ProjectExecution,
    room_id: str,
    *,
    ancestor_room_id: str | None = None,
) -> Room:
    target = await db.get(Room, room_id)
    cursor = target
    visited = set()
    while cursor is not None and cursor.id not in visited:
        if (
            cursor.project_id != execution.project_id
            or cursor.is_dm
            or cursor.archived_at is not None
        ):
            break
        if cursor.id == (ancestor_room_id or execution.operating_room_id):
            if target.id == cursor.id:
                break
            return target
        visited.add(cursor.id)
        cursor = (
            await db.get(Room, cursor.parent_room_id) if cursor.parent_room_id else None
        )
    _reject(
        "DELEGATION_ROOM_OUT_OF_SCOPE",
        "Target must be an active descendant room in this project",
    )


async def delegate_task(
    db: AsyncSession,
    *,
    agent_id: str,
    proof: TurnProof,
    execution_id: str,
    parent_task_id: str,
    target_room_id: str,
    assignee_participant_id: str,
    delegation_key: str,
    title: str,
    spec: str,
    role: str = "work",
    required: bool = True,
    depends_on: list[str] | None = None,
    qa_target_task_id: str | None = None,
    qa_target_result_version: int | None = None,
) -> Task:
    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    execution = await _execution(db, execution_id)
    _active(execution)
    parent = await db.get(Task, parent_task_id)
    if (
        parent is None
        or turn.task_id != parent.id
        or parent.execution_id != execution.id
        or parent.assignee_participant_id != turn.target_participant_id
        or parent.room_id != turn.room_id
        or parent.input_revision != execution.input_revision
        or parent.status != "in_progress"
    ):
        _reject(
            "DELEGATION_PARENT_PROOF_INVALID",
            "Delegate only from your bound active task at the current input revision",
        )
    await assert_task_workflow_ready(db, task=parent, proof=proof, operation="delegate")
    if (
        not delegation_key
        or len(delegation_key) > 160
        or not title.strip()
        or not spec.strip()
    ):
        _reject(
            "DELEGATION_INPUT_INVALID",
            "A stable key, title and concrete assignment are required",
        )
    room = await _descendant_room(
        db, execution, target_room_id, ancestor_room_id=parent.room_id
    )
    participant = await db.get(Participant, assignee_participant_id)
    agent = (
        await db.get(Agent, participant.agent_id)
        if participant and participant.agent_id
        else None
    )
    if (
        participant is None
        or participant.room_id != room.id
        or agent is None
        or agent.desired_state != "running"
    ):
        _reject(
            "DELEGATION_ASSIGNEE_INVALID",
            "Assignee must be a running agent participant of the target room",
        )
    if role not in INTERNAL_TASK_ROLES:
        _reject("DELEGATION_ROLE_INVALID", "Unsupported execution task role")
    require_execution_operation(execution, role)
    declared_dependencies = list(dict.fromkeys(depends_on or []))
    if role == "qa":
        depends_on = [*dict.fromkeys([*(depends_on or []), qa_target_task_id])]
    snapshot = await db.scalar(
        select(ExecutionInputRevision).where(
            ExecutionInputRevision.execution_id == execution.id,
            ExecutionInputRevision.revision == execution.input_revision,
        )
    )
    full_spec = (
        f"Project execution {execution.id}; input revision {snapshot.revision}.\n"
        f"Objective:\n{snapshot.objective}\n\nOriginal user request and constraints:\n{snapshot.user_constraints}\n\n"
        f"Assignment:\n{spec}\n\nCompletion criteria:\n{snapshot.completion_criteria}\n"
        "Return your actual result via mark_task_status(status=done, result_markdown=...). "
        "Do not mark done before producing the required result."
    )
    full_spec += f"\nPrerequisite task IDs: {declared_dependencies}"
    for item in snapshot.input_files:
        full_spec += f"\n\nInput file {item['filename']} (SHA-256 {item['sha256']}):\n"
        full_spec += item.get(
            "content", f"Base64 original bytes: {item.get('content_base64', '')}"
        )
    # Include QA target bytes before comparing an idempotent replay's spec.
    if role == "qa":
        original_result = await db.scalar(
            select(TaskResult).where(
                TaskResult.task_id == qa_target_task_id,
                TaskResult.version == qa_target_result_version,
            )
        )
        if original_result is not None:
            full_spec += (
                f"\n\nQA target task {qa_target_task_id}, result version {original_result.version}, "
                f"SHA-256 {original_result.result_sha256}:\n{original_result.result_markdown}\n"
                "Report verification={verdict: pass|fail, target_task_id: ..., target_result_version: ...}."
            )
    existing = await db.scalar(
        select(Task).where(
            Task.execution_id == execution.id,
            Task.input_revision == execution.input_revision,
            Task.delegation_key == delegation_key
        )
    )
    if existing is not None:
        declaration = await db.scalar(
            select(ProjectExecutionEvent).where(
                ProjectExecutionEvent.event_key
                == f"execution:{execution.id}:delegated:{existing.id}",
            )
        )
        declared_spec_hash = (
            declaration.details.get("spec_sha256") if declaration else None
        )
        same_spec = (
            declared_spec_hash == hashlib.sha256(full_spec.encode("utf-8")).hexdigest()
            if declared_spec_hash
            else existing.spec == full_spec
        )
        declared_qa_version = (
            declaration.details.get("qa_declared_version")
            if declaration and "qa_declared_version" in declaration.details
            else existing.qa_target_result_version
        )
        if (
            existing.parent_task_id != parent.id
            or existing.room_id != room.id
            or existing.assignee_participant_id != participant.id
            or existing.title != title.strip()[:500]
            or not same_spec
            or existing.role != role
            or existing.required_for_execution != required
            or existing.qa_target_task_id != qa_target_task_id
            or declared_qa_version != qa_target_result_version
            or (declaration is not None and "qa_deferred" in declaration.details
                and declaration.details["qa_deferred"] != (role == "qa" and qa_target_result_version is None))
        ):
            _reject(
                "DELEGATION_KEY_CONFLICT",
                "Stable delegation key was reused for a different assignment",
            )
        return existing
    if role == "qa":
        from anygarden.project_executions.qa_targets import validate_target_declaration

        # An identical historical declaration remains idempotent after its
        # target is repaired; only a new declaration requires a current target.
        await validate_target_declaration(
            db, execution=execution, parent=parent, reviewer_agent_id=agent.id,
            target_task_id=qa_target_task_id, declared_version=qa_target_result_version,
        )
    from anygarden.project_executions.usage import admission_disposition

    admission = await admission_disposition(db, execution=execution, phase="intent")
    if not admission.allowed:
        reason = admission.reason_code or "EXECUTION_NATIVE_ADMISSION_DENIED"
        db.info.setdefault("project_execution_usage_denials", {})[execution.id] = reason
        _reject(reason, "The execution cannot create more managed work within its limits")
    depth = parent.delegation_depth + 1
    if depth > execution.limits.get("max_depth", 8):
        _reject(
            "EXECUTION_DEPTH_LIMIT", "Execution delegation depth limit has been reached"
        )
    prerequisites = []
    for task_id in dict.fromkeys(depends_on or []):
        prerequisite = await db.get(Task, task_id)
        if (
            prerequisite is None
            or prerequisite.execution_id != execution.id
            or prerequisite.input_revision != execution.input_revision
        ):
            _reject(
                "DELEGATION_DEPENDENCY_INVALID",
                "Prerequisites must belong to this execution and input revision",
            )
        if prerequisite.id == parent.id:
            _reject(
                "DELEGATION_DEPENDENCY_CYCLE",
                "A child cannot depend on its active parent",
            )
        prerequisites.append(prerequisite)
    changed = await db.execute(
        update(ProjectExecution)
        .where(
            ProjectExecution.id == execution.id,
            ProjectExecution.status.in_(ACTIVE_EXECUTION_STATES),
            ProjectExecution.input_revision == execution.input_revision,
            ProjectExecution.state_revision == execution.state_revision,
            or_(
                ProjectExecution.deadline_at.is_(None),
                ProjectExecution.deadline_at > _now(),
            ),
            delegation_total_expression(execution.id)
            < execution.limits.get("max_delegations", 100),
        )
        .values(
            delegation_count=delegation_total_expression(execution.id) + 1,
            state_revision=ProjectExecution.state_revision + 1,
            status="running",
            plan_sealed=False,
            updated_at=_now(),
        )
        .returning(ProjectExecution.id)
    )
    if changed.scalar_one_or_none() is None:
        _reject(
            "EXECUTION_DELEGATION_LIMIT",
            "Execution changed or delegation limit has been reached",
        )
    child = Task(
        room_id=room.id,
        title=title.strip()[:500],
        execution_id=execution.id,
        parent_task_id=parent.id,
        input_revision=execution.input_revision,
        delegation_depth=depth,
        delegation_key=delegation_key,
        role=role,
        required_for_execution=required,
        qa_target_task_id=qa_target_task_id,
        qa_target_result_version=qa_target_result_version,
        assignee_participant_id=participant.id,
        created_by=execution.owner_user_id,
        triggered_by="project_execution",
        spec=full_spec,
        goal_id=None,
        assigned_at=_now(),
    )
    db.add(child)
    await db.flush()
    accepted = []
    for prerequisite in prerequisites:
        result = await db.scalar(
            select(TaskResult).where(
                TaskResult.task_id == prerequisite.id,
                TaskResult.version == prerequisite.result_version,
            )
        )
        if prerequisite.status == "done" and result is not None:
            accepted.append(
                {
                    "task_id": prerequisite.id,
                    "title": prerequisite.title,
                    "room_id": prerequisite.room_id,
                    "execution_id": execution.id,
                    "input_revision": result.input_revision,
                    "result_id": result.id,
                    "result_version": result.version,
                    "result_sha256": result.result_sha256,
                    "result_markdown": result.result_markdown,
                    "artifacts": result.artifacts,
                    "finished_at": prerequisite.finished_at.isoformat()
                    if prerequisite.finished_at
                    else None,
                    "result_created_at": result.created_at.isoformat(),
                }
            )
        else:
            db.add(TaskBlocker(task_id=child.id, blocked_by_task_id=prerequisite.id))
            child.status = "blocked"
    child.dependency_results = accepted or None
    await db.flush()
    if child.status != "blocked":
        child = await claim_task_cas(
            db, task_id=child.id, room_id=room.id, participant_id=participant.id
        )
        message = await inject_task_assignment_message(
            db, room=room, task=child, sender_participant_id=None
        )
        _queue_message(db, message)
    await _event(
        db,
        execution,
        f"execution:{execution.id}:delegated:{child.id}",
        "task_delegated",
        task_id=child.id,
        details={
            "parent_task_id": parent.id,
            "input_revision": child.input_revision,
            "spec_sha256": hashlib.sha256(full_spec.encode("utf-8")).hexdigest(),
            "depends_on": list(dict.fromkeys(depends_on or [])),
            "declared_depends_on": declared_dependencies,
            "qa_declared_version": qa_target_result_version,
            "qa_deferred": role == "qa" and qa_target_result_version is None,
        },
    )
    await db.flush()
    return child


async def _pending_requests(db: AsyncSession, task: Task) -> bool:
    # Registration is separate so the question/answer seam can evolve without
    # changing the immutable result and lineage tables.
    from anygarden.db.execution_request_models import ExecutionRequest

    return bool(
        await db.scalar(
            select(ExecutionRequest.id).where(
                ExecutionRequest.task_id == task.id,
                ExecutionRequest.input_revision == task.input_revision,
                ExecutionRequest.status == "pending",
            )
        )
    )


async def _assert_approval_source_current(
    db: AsyncSession, execution: ProjectExecution, approval: Any
) -> None:
    """A successful action is evidence only for the exact approved source bytes."""
    source = await db.get(Task, approval.source_task_id)
    result = await db.get(TaskResult, approval.source_result_id)
    artifact = await db.get(RoomArtifact, approval.artifact_id)
    source_room = await db.get(Room, source.room_id) if source else None
    artifact_room = await db.get(Room, artifact.room_id) if artifact else None
    if (
        source is None
        or source.execution_id != execution.id
        or source.status != "done"
        or source.input_revision != execution.input_revision
        or source.result_version != approval.source_result_version
        or source_room is None
        or source_room.project_id != execution.project_id
        or result is None
        or result.task_id != source.id
        or result.execution_id != execution.id
        or result.input_revision != execution.input_revision
        or result.version != approval.source_result_version
        or result.result_sha256 != approval.source_result_sha256
        or artifact is None
        or artifact.room_id != approval.artifact_room_id
        or artifact.sha256 != approval.artifact_sha256
        or artifact_room is None
        or artifact_room.project_id != execution.project_id
        or not any(
            isinstance(reference, dict)
            and reference.get("artifact_id") == artifact.id
            and reference.get("sha256") == artifact.sha256
            for reference in result.artifacts or []
        )
        or approval.decision != "approve"
        or approval.decided_by_user_id is None
        or approval.executed_at is None
        or approval.finished_at is None
        or approval.executing_turn_id is None
    ):
        _reject(
            "APPROVAL_SOURCE_STALE",
            "Completion requires the successful managed action for its exact approved source result and artifact",
        )
    require_execution_operation(execution, f"managed_{approval.action_kind}")


async def assert_task_workflow_ready(
    db: AsyncSession,
    *,
    task: Task,
    proof: TurnProof | None = None,
    operation: str,
) -> None:
    """Fence writes/delegations behind questions and managed human decisions.

    The action executor must use its approved permit instead: even an approved
    action blocks normal task work until the server has recorded its outcome.
    """
    from anygarden.db.execution_approval_models import ExecutionApproval
    from anygarden.db.execution_request_models import ExecutionRequest

    execution = await _execution(db, task.execution_id)
    _active(execution)
    if task.input_revision != execution.input_revision:
        _reject("TASK_INPUT_SUPERSEDED", "Task input revision is no longer current")
    if operation == "child_handoff" and await db.scalar(select(TaskBlocker.task_id).where(
        TaskBlocker.task_id == task.id,
    ).limit(1)):
        _reject("PARENT_HANDOFF_DEPENDENCY_BLOCKED",
                "Parent handoff must wait for its existing prerequisite blockers")
    if task.dependency_results or await db.scalar(select(ProjectExecutionEvent.id).where(
        ProjectExecutionEvent.execution_id == execution.id,
        ProjectExecutionEvent.task_id == task.id,
        ProjectExecutionEvent.event_type == CHILD_HANDOFF_EVENT,
    ).limit(1)):
        # Reading a frozen handoff and accepting derived output must enforce
        # the same exact-version proof, including during final collection.
        await get_bound_task_execution_detail(
            db, execution_id=execution.id, task_id=task.id,
        )
    if operation == "artifact":
        require_execution_operation(execution, "publish_artifact")
    elif operation == "result" and task.id != execution.root_task_id:
        require_execution_operation(execution, task.role or "work")
    if await _pending_requests(db, task):
        _reject(
            "TASK_WAITING_FOR_USER",
            "Pending user questions must be answered before continuing this task",
        )
    approvals = list(
        await db.scalars(
            select(ExecutionApproval).where(
                ExecutionApproval.execution_id == execution.id,
                ExecutionApproval.task_id == task.id,
                ExecutionApproval.input_revision == task.input_revision,
            )
        )
    )
    unresolved = [approval for approval in approvals if approval.status != "succeeded"]
    if unresolved:
        _reject(
            "TASK_APPROVAL_UNRESOLVED",
            "The managed action has not succeeded: "
            + ", ".join(f"{approval.id} ({approval.status})" for approval in unresolved),
        )
    if proof is not None:
        superseded = await db.scalar(
            select(ExecutionRequest.id).where(
                ExecutionRequest.task_id == task.id,
                ExecutionRequest.input_revision == task.input_revision,
                ExecutionRequest.turn_request_id == proof.request_id,
                ExecutionRequest.resume_message_id.is_not(None),
            )
        )
        if superseded or any(
            approval.turn_request_id == proof.request_id
            and approval.resume_message_id is not None
            for approval in approvals
        ):
            _reject(
                f"{operation.upper()}_CONTEXT_SUPERSEDED",
                "The answer or approval-bearing continuation must perform this operation",
            )
    if operation == "result" and task.role == "release" and not approvals:
        _reject(
            "RELEASE_ACTION_NOT_EXECUTED",
            "A release task requires an approved managed action with a recorded successful outcome",
        )
    for approval in approvals:
        await _assert_approval_source_current(db, execution, approval)


async def _assert_execution_approvals(
    db: AsyncSession, execution: ProjectExecution
) -> None:
    from anygarden.db.execution_approval_models import ExecutionApproval

    approvals = list(
        await db.scalars(
            select(ExecutionApproval).where(
                ExecutionApproval.execution_id == execution.id,
                ExecutionApproval.input_revision == execution.input_revision,
            )
        )
    )
    if any(approval.status != "succeeded" for approval in approvals):
        _reject(
            "EXECUTION_APPROVAL_UNRESOLVED",
            "Every requested managed action must succeed before completing this execution",
        )
    for approval in approvals:
        await _assert_approval_source_current(db, execution, approval)


async def _validate_artifacts(
    db: AsyncSession,
    execution: ProjectExecution,
    task: Task,
    artifacts: list,
) -> None:
    events = list(
        (
            await db.scalars(
                select(ProjectExecutionEvent).where(
                    ProjectExecutionEvent.execution_id == execution.id,
                    ProjectExecutionEvent.task_id == task.id,
                    ProjectExecutionEvent.event_type == "artifact_published",
                )
            )
        ).all()
    )
    published = [
        item for event in events for item in event.details.get("artifacts", [])
    ]
    for reference in artifacts:
        if not isinstance(reference, dict):
            _reject(
                "ARTIFACT_REFERENCE_INVALID",
                "Artifact references must contain their persisted IDs and hashes",
            )
        artifact = (
            await db.get(RoomArtifact, reference.get("artifact_id"))
            if reference.get("artifact_id")
            else None
        )
        room = await db.get(Room, artifact.room_id) if artifact else None
        if (
            artifact is None
            or room is None
            or room.project_id != execution.project_id
            or room.id not in {execution.operating_room_id, task.room_id}
            or reference.get("sha256") != artifact.sha256
            or reference.get("room_id") != artifact.room_id
            or reference.get("task_id") != task.id
            or reference.get("execution_id") != execution.id
            or reference.get("input_revision") != task.input_revision
            or not any(reference == item for item in published)
        ):
            _reject(
                "ARTIFACT_PROVENANCE_INVALID",
                "Result artifacts must be published by this task at the current input revision",
            )


async def finalize_task_result(
    db: AsyncSession,
    task: Task,
    result_markdown: str,
    *,
    agent_id: str,
    proof: TurnProof,
    artifacts: list | None = None,
    verification: dict | None = None,
    _allow_root: bool = False,
) -> TaskResult:
    """Accept actual result bytes once per producing attempt, before done CAS."""
    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    execution = await _execution(db, task.execution_id)
    if (
        turn.task_id != task.id
        or turn.room_id != task.room_id
        or turn.target_participant_id != task.assignee_participant_id
        or task.input_revision != execution.input_revision
    ):
        _reject(
            "RESULT_TURN_MISMATCH",
            "Result must come from the task's bound current invocation",
        )
    if task.id == execution.root_task_id and not _allow_root:
        _reject(
            "ROOT_COMPLETION_GATE",
            "Use complete_project_execution after sealing and collecting required results",
        )
    if not isinstance(result_markdown, str) or not result_markdown.strip():
        _reject(
            "RESULT_REQUIRED", "A nonempty actual result is required for execution work"
        )
    await assert_task_workflow_ready(db, task=task, proof=proof, operation="result")
    attempt = await db.scalar(
        select(AgentTurnAttempt).where(
            AgentTurnAttempt.turn_id == proof.request_id,
            AgentTurnAttempt.attempt_number == proof.attempt,
        )
    )
    digest = hashlib.sha256(result_markdown.encode("utf-8")).hexdigest()
    existing = await db.scalar(
        select(TaskResult).where(
            TaskResult.task_id == task.id, TaskResult.attempt_id == attempt.id
        )
    )
    from anygarden.project_executions.result_artifacts import (
        replay_result_artifacts,
        resolve_result_artifacts,
    )

    if existing is not None:
        frozen_artifacts = replay_result_artifacts(
            artifacts=artifacts, frozen=list(existing.artifacts or []),
        )
        if (
            existing.result_sha256 != digest
            or existing.verification != verification
            or existing.artifacts != frozen_artifacts
        ):
            _reject(
                "RESULT_REPLAY_CONFLICT", "An attempt's accepted result is immutable"
            )
        return existing
    _active(execution)
    if task.status != "in_progress":
        _reject(
            "RESULT_TASK_NOT_ACTIVE",
            "Only a claimed in-progress task can submit a new result",
        )
    if task.result_version:
        from anygarden.project_executions.qa_repairs import repair_intent_for_turn

        intent = await repair_intent_for_turn(db, turn=turn, attempt=attempt)
        if (intent is None or intent["base_result_version"] != task.result_version
            or intent["expected_result_version"] != task.result_version + 1):
            _reject(
                "RESULT_REPAIR_INTENT_REQUIRED",
                "A new result version requires this exact reserved repair assignment",
            )
    canonical_artifacts = await resolve_result_artifacts(
        db, execution=execution, task=task, agent_id=agent_id, proof=proof,
        attempt_id=attempt.id, artifacts=artifacts,
    )
    await _validate_artifacts(db, execution, task, canonical_artifacts)
    if task.role == "qa":
        target = await db.get(Task, task.qa_target_task_id)
        target_result = await db.scalar(
            select(TaskResult).where(
                TaskResult.task_id == task.qa_target_task_id,
                TaskResult.version == task.qa_target_result_version,
            )
        )
        if (
            target is None
            or target_result is None
            or target.status != "done"
            or target.result_version != task.qa_target_result_version
            or target_result.input_revision != execution.input_revision
            or target_result.producer_agent_id == agent_id
            or not isinstance(verification, dict)
            or verification.get("verdict") not in {"pass", "fail"}
            or verification.get("target_task_id") != target.id
            or type(verification.get("target_result_version")) is not int
            or verification.get("target_result_version") != target_result.version
        ):
            _reject(
                "QA_VERIFICATION_REQUIRED",
                "QA must identify the exact current result version and independent pass/fail verdict",
            )
    version = task.result_version + 1
    changed = await db.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.status == "in_progress",
            Task.result_version == task.result_version,
            Task.input_revision == execution.input_revision,
            Task.assignee_participant_id == turn.target_participant_id,
        )
        .values(result_version=version, result_markdown=result_markdown)
        .returning(Task.id)
    )
    if changed.scalar_one_or_none() is None:
        _reject(
            "RESULT_VERSION_CONFLICT",
            "Task changed before its result could be accepted",
        )
    result = TaskResult(
        execution_id=execution.id,
        task_id=task.id,
        version=version,
        input_revision=execution.input_revision,
        attempt_id=attempt.id,
        producer_agent_id=agent_id,
        result_markdown=result_markdown,
        result_sha256=digest,
        artifacts=canonical_artifacts,
        verification=verification,
    )
    db.add(result)
    await db.flush()
    await _event(
        db,
        execution,
        f"execution:{execution.id}:result:{result.id}",
        "result_accepted",
        task_id=task.id,
        details={
            "result_id": result.id,
            "version": version,
            "input_revision": execution.input_revision,
            "sha256": digest,
        },
    )
    return result


async def _validate_child_handoff(
    db: AsyncSession, *, execution: ProjectExecution, parent: Task,
    event: ProjectExecutionEvent,
) -> tuple[Task, TaskResult, Message]:
    """Validate the server-recorded direct-child grant, never room proximity."""
    details = event.details if isinstance(event.details, dict) else {}
    child = await db.get(Task, details.get("child_task_id")) if details.get("child_task_id") else None
    result = await db.get(TaskResult, details.get("result_id")) if details.get("result_id") else None
    participant = await db.get(Participant, parent.assignee_participant_id) if parent.assignee_participant_id else None
    producer = await db.get(Participant, child.assignee_participant_id) if child and child.assignee_participant_id else None
    parent_room = await db.get(Room, parent.room_id)
    child_room = await db.get(Room, child.room_id) if child else None
    message = await db.get(Message, event.message_id) if event.message_id else None
    assignment = await db.get(AgentTurn, details.get("parent_assignment_request_id")) if details.get("parent_assignment_request_id") else None
    assignment_source = await db.get(Message, details.get("parent_assignment_source_message_id")) if details.get("parent_assignment_source_message_id") else None
    metadata = message.extra_metadata if message and isinstance(message.extra_metadata, dict) else {}
    if (event.execution_id != execution.id or event.task_id != parent.id
        or details.get("parent_task_id") != parent.id
        or details.get("parent_room_id") != parent.room_id
        or details.get("parent_participant_id") != parent.assignee_participant_id
        or type(details.get("input_revision")) is not int
        or details["input_revision"] != execution.input_revision
        or parent.execution_id != execution.id or parent.input_revision != execution.input_revision
        or participant is None or participant.room_id != parent.room_id
        or participant.agent_id != details.get("parent_agent_id")
        or participant.role not in AGENT_EXECUTION_ROLES
        or child is None or child.parent_task_id != parent.id or child.id == parent.id
        or child.execution_id != execution.id or child.input_revision != execution.input_revision
        or child.status != "done" or result is None
        or result.task_id != child.id or result.execution_id != execution.id
        or result.input_revision != execution.input_revision
        or type(details.get("result_version")) is not int
        or result.version != details["result_version"] or child.result_version != result.version
        or result.result_sha256 != details.get("result_sha256")
        or hashlib.sha256(result.result_markdown.encode()).hexdigest() != result.result_sha256
        or producer is None or producer.room_id != child.room_id
        or producer.agent_id != result.producer_agent_id
        or parent_room is None or parent_room.project_id != execution.project_id
        or parent_room.archived_at is not None
        or child_room is None or child_room.project_id != execution.project_id
        or child_room.archived_at is not None
        or assignment is None or assignment_source is None
        or assignment.task_id != parent.id or assignment.execution_id != execution.id
        or assignment.execution_input_revision != execution.input_revision
        or assignment.agent_id != participant.agent_id
        or assignment.target_participant_id != participant.id or assignment.room_id != parent.room_id
        or assignment.trigger_message_id != assignment_source.id
        or assignment_source.room_id != parent.room_id
        or "thread_root_id" not in details
        or assignment.thread_root_id != details["thread_root_id"]
        or message is None or message.room_id != parent.room_id
        or message.root_message_id != details.get("thread_root_id")
        or hashlib.sha256(message.content.encode()).hexdigest() != details.get("message_sha256")
        or metadata.get("system_origin") != CHILD_HANDOFF_ORIGIN
        or metadata.get("execution_event_id") != event.id
        or metadata.get("execution_id") != execution.id
        or metadata.get("input_revision") != execution.input_revision
        or metadata.get("parent_task_id") != parent.id
        or metadata.get("parent_assignment_request_id") != assignment.request_id
        or metadata.get("parent_assignment_source_message_id") != assignment_source.id
        or metadata.get("source_task_id") != child.id
        or metadata.get("accepted_result_id") != result.id
        or metadata.get("ingest_only") is not True or metadata.get("mentions") != []):
        _reject("CHILD_HANDOFF_SCOPE_INVALID", "The recorded direct-child result handoff is stale or outside this task")
    await _descendant_room(db, execution, child.room_id, ancestor_room_id=parent.room_id)
    if child.role == "qa" and (result.verification or {}).get("verdict") != "pass":
        _reject("CHILD_QA_NOT_PASSED", "A failed independent review is not a successful child input")
    if child.role == "qa":
        target = await db.get(Task, child.qa_target_task_id) if child.qa_target_task_id else None
        target_result = await db.scalar(select(TaskResult).where(
            TaskResult.task_id == child.qa_target_task_id,
            TaskResult.execution_id == execution.id,
            TaskResult.input_revision == execution.input_revision,
            TaskResult.version == child.qa_target_result_version,
        ))
        verification = result.verification or {}
        target_room = await db.get(Room, target.room_id) if target else None
        if (target is None or target.execution_id != execution.id
            or target.input_revision != execution.input_revision or target.status != "done"
            or type(child.qa_target_result_version) is not int or child.qa_target_result_version < 1
            or target.result_version != child.qa_target_result_version or target_result is None
            or target_result.producer_agent_id == result.producer_agent_id
            or target_room is None or target_room.project_id != execution.project_id
            or target_room.archived_at is not None
            or verification.get("target_task_id") != target.id
            or type(verification.get("target_result_version")) is not int
            or verification["target_result_version"] != target_result.version
            or hashlib.sha256(target_result.result_markdown.encode()).hexdigest() != target_result.result_sha256):
            _reject("CHILD_QA_TARGET_STALE", "A successful child review must still bind its exact independent current target")
    producing_turn = await db.scalar(select(AgentTurn).join(AgentTurnAttempt,
        AgentTurnAttempt.turn_id == AgentTurn.request_id).where(
        AgentTurnAttempt.id == result.attempt_id,
        AgentTurnAttempt.agent_id == result.producer_agent_id,
        AgentTurn.task_id == child.id, AgentTurn.execution_id == execution.id,
        AgentTurn.execution_input_revision == execution.input_revision,
        AgentTurn.agent_id == result.producer_agent_id,
        AgentTurn.target_participant_id == child.assignee_participant_id,
        AgentTurn.room_id == child.room_id,
    ))
    if producing_turn is None:
        _reject("CHILD_HANDOFF_RESULT_UNPROVED", "Child input requires its exact accepted producer attempt")
    await _validate_artifacts(db, execution, child, list(result.artifacts or []))
    return child, result, message


async def get_bound_direct_child_results(
    db: AsyncSession, *, execution: ProjectExecution, task: Task,
) -> list[dict]:
    """Public evidence only; callers first authorize their exact bound task."""
    events = list(await db.scalars(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution.id,
        ProjectExecutionEvent.task_id == task.id,
        ProjectExecutionEvent.event_type == CHILD_HANDOFF_EVENT,
    ).order_by(ProjectExecutionEvent.created_at, ProjectExecutionEvent.id)))
    latest = {}
    for event in events:
        details = event.details if isinstance(event.details, dict) else {}
        if details.get("input_revision") == execution.input_revision:
            latest[details.get("child_task_id")] = event
    snapshots = []
    for event in latest.values():
        child, result, message = await _validate_child_handoff(
            db, execution=execution, parent=task, event=event,
        )
        snapshots.append({
            "task_id": child.id, "parent_task_id": task.id, "title": child.title,
            "room_id": child.room_id, "execution_id": execution.id,
            "input_revision": result.input_revision, "result_id": result.id,
            "result_version": result.version, "result_sha256": result.result_sha256,
            "result_markdown": result.result_markdown, "artifacts": result.artifacts,
            "verification": result.verification,
            "producer_agent_id": result.producer_agent_id,
            "handoff_event_id": event.id, "handoff_message_id": message.id,
        })
    return snapshots


async def validate_parent_handoff_turn(db, turn: AgentTurn) -> tuple[bool, str | None]:
    """New native start only. Do not use for an already-permitted completion.

    Root's native-start service calls this before reserving a new invocation;
    normal agent lease/outbox delivery still serializes an open parent turn.
    """
    message = await db.get(Message, turn.trigger_message_id)
    metadata = message.extra_metadata if message and isinstance(message.extra_metadata, dict) else {}
    if metadata.get("system_origin") != CHILD_HANDOFF_ORIGIN:
        return True, None
    try:
        parent = await db.get(Task, turn.task_id) if turn.task_id else None
        execution = await _execution(db, turn.execution_id) if turn.execution_id else None
        event = await db.get(ProjectExecutionEvent, metadata.get("execution_event_id")) if metadata.get("execution_event_id") else None
        if (parent is None or execution is None or event is None
            or parent.status != "in_progress" or parent.result_version
            or turn.room_id != parent.room_id
            or turn.target_participant_id != parent.assignee_participant_id
            or parent.source_message_id != (event.details or {}).get("parent_assignment_source_message_id")
            or turn.execution_input_revision != execution.input_revision
            or turn.idempotency_key != event.event_key + ":wake"
            or turn.thread_root_id != message.root_message_id
            or event.message_id != message.id):
            _reject("PARENT_HANDOFF_NOT_ACTIVE", "The parent handoff no longer authorizes a new invocation")
        _, _, _ = await _validate_child_handoff(db, execution=execution, parent=parent, event=event)
        participant = await db.get(Participant, parent.assignee_participant_id)
        if participant.agent_id != turn.agent_id:
            _reject("PARENT_HANDOFF_NOT_ACTIVE", "Parent handoff participant changed")
        await assert_task_workflow_ready(db, task=parent, operation="child_handoff")
    except ExecutionConflict as exc:
        return False, exc.code
    return True, None


async def _resume_parent_handoff(db, *, execution, parent, event, message) -> None:
    from anygarden.project_executions.qa_repairs import repair_savepoint

    wake_key = event.event_key + ":wake"
    if await db.scalar(select(ProjectExecutionEvent.id).where(
        ProjectExecutionEvent.event_key == wake_key,
    )):
        return
    try:
        async with repair_savepoint(db):
            _, _, _ = await _validate_child_handoff(db, execution=execution, parent=parent, event=event)
            participant = await db.get(Participant, parent.assignee_participant_id)
            agent = await db.get(Agent, participant.agent_id)
            if (parent.status != "in_progress" or parent.result_version
                or parent.source_message_id != (event.details or {}).get("parent_assignment_source_message_id")
                or agent is None or agent.desired_state != "running"
                or agent.actual_state != "running" or agent.pending_generation is not None):
                _reject("PARENT_HANDOFF_NOT_ACTIVE", "Parent task or its original producer cannot currently resume")
            await assert_task_workflow_ready(db, task=parent, operation="child_handoff")
            continuation = await create_turn(
                db, room_id=parent.room_id, participant_id=participant.id,
                agent_id=participant.agent_id, trigger_message_id=message.id,
                thread_root_id=message.root_message_id, task_id=parent.id,
                idempotency_key=wake_key,
            )
            if (continuation.task_id != parent.id or continuation.execution_id != execution.id
                or continuation.execution_input_revision != parent.input_revision
                or continuation.room_id != parent.room_id
                or continuation.target_participant_id != participant.id
                or continuation.agent_id != participant.agent_id
                or continuation.trigger_message_id != message.id
                or continuation.thread_root_id != message.root_message_id):
                _reject("PARENT_HANDOFF_CONTEXT_CHANGED", "Existing continuation does not match this handoff")
            if continuation.state not in OPEN_TURN_STATES:
                _reject(continuation.terminal_reason or "PARENT_HANDOFF_DEFERRED",
                        "The parent continuation is fenced by current workflow or admission")
            wake = await _event(db, execution, wake_key, "task_child_result_parent_wake",
                task_id=parent.id, details={"handoff_event_id": event.id,
                    "input_revision": parent.input_revision, "continuation_request_id": continuation.request_id})
            if wake is not None:
                wake.message_id = message.id
                db.info.setdefault("project_execution_recovery_tasks", set()).add(parent.id)
    except Exception as exc:
        await db.refresh(execution)
        await db.refresh(parent)
        await db.refresh(event)
        await db.refresh(message)
        code = exc.code if isinstance(exc, ExecutionConflict) else "PARENT_HANDOFF_DEFERRED"
        if not isinstance(exc, ExecutionConflict):
            log.warning("project_execution.parent_handoff_deferred code=%s", type(exc).__name__)
        await _event(db, execution, wake_key + ":blocked:" + code,
            "task_child_result_parent_wake_blocked", task_id=parent.id,
            details={"handoff_event_id": event.id, "input_revision": parent.input_revision,
                     "reason_code": code})


async def _handoff_child_result(
    db: AsyncSession, *, execution: ProjectExecution, child: Task, result: TaskResult | None,
) -> list[Message]:
    """Keep the root report and add one authenticated successful parent input."""
    if (child.status != "done" or result is None or child.parent_task_id is None
        or child.parent_task_id == execution.root_task_id
        or (child.role == "qa" and (result.verification or {}).get("verdict") != "pass")):
        return []
    # Same execution mutex as result acceptance/revision/cancel, before parent.
    locked = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == execution.id, ProjectExecution.input_revision == child.input_revision,
        ProjectExecution.status.in_(ACTIVE_EXECUTION_STATES),
        or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > _now()),
    ).values(state_revision=ProjectExecution.state_revision).returning(ProjectExecution.id))
    if locked is None:
        return []
    parent = await db.get(Task, child.parent_task_id, populate_existing=True, with_for_update=True)
    if (parent is None or parent.execution_id != execution.id
        or parent.input_revision != execution.input_revision or parent.status not in {"in_progress", "blocked"}):
        return []
    participant = await db.get(Participant, parent.assignee_participant_id) if parent.assignee_participant_id else None
    room = await db.get(Room, parent.room_id)
    source = await db.get(Message, parent.source_message_id) if parent.source_message_id else None
    if (participant is None or participant.room_id != parent.room_id or participant.agent_id is None
        or participant.role not in AGENT_EXECUTION_ROLES or room is None
        or room.project_id != execution.project_id or room.archived_at is not None
        or source is None or source.room_id != parent.room_id):
        return []
    if (result.task_id != child.id or result.execution_id != execution.id
        or result.input_revision != execution.input_revision or result.version != child.result_version
        or hashlib.sha256(result.result_markdown.encode()).hexdigest() != result.result_sha256):
        _reject("CHILD_HANDOFF_RESULT_UNPROVED", "Only the child's exact accepted result can be handed to its parent")
    await _descendant_room(db, execution, child.room_id, ancestor_room_id=parent.room_id)
    await _validate_artifacts(db, execution, child, list(result.artifacts or []))
    assignments = list(await db.scalars(select(AgentTurn).where(
        AgentTurn.task_id == parent.id, AgentTurn.execution_id == execution.id,
        AgentTurn.execution_input_revision == execution.input_revision,
        AgentTurn.agent_id == participant.agent_id,
        AgentTurn.target_participant_id == participant.id, AgentTurn.room_id == parent.room_id,
        AgentTurn.trigger_message_id == source.id,
    ).limit(2)))
    if len(assignments) != 1:
        _reject("PARENT_HANDOFF_ASSIGNMENT_INVALID", "Parent continuation requires one exact original assignment turn")
    assignment = assignments[0]
    key = f"execution:{execution.id}:parent:{parent.id}:child:{child.id}:r{child.input_revision}:v{result.version}:handoff"
    event = await _event(db, execution, key, CHILD_HANDOFF_EVENT, task_id=parent.id,
        details={"parent_task_id": parent.id, "parent_room_id": parent.room_id,
            "parent_participant_id": participant.id, "parent_agent_id": participant.agent_id,
            "child_task_id": child.id, "input_revision": child.input_revision,
            "result_id": result.id, "result_version": result.version,
            "result_sha256": result.result_sha256,
            "parent_assignment_request_id": assignment.request_id,
            "parent_assignment_source_message_id": source.id,
            "thread_root_id": assignment.thread_root_id})
    created = event is not None
    if event is None:
        event = await db.scalar(select(ProjectExecutionEvent).where(ProjectExecutionEvent.event_key == key))
        _, _, message = await _validate_child_handoff(db, execution=execution, parent=parent, event=event)
    else:
        text = (f"Continue your existing parent Task {parent.id}; do not create a replacement task.\n"
            f"Execution {execution.id}, input revision {child.input_revision}.\n"
            f"Your direct child Task {child.id} ({child.title}) completed with accepted result {result.id}, "
            f"version {result.version}, SHA-256 {result.result_sha256}.\n{result.result_markdown}\n"
            f"Published artifact references: {result.artifacts or []}\n"
            "This is successful child evidence, separate from ordinary prerequisites and failed QA feedback. "
            "Use get_project_execution for your bound child_results and read_project_artifact for these exact files. "
            "Collect the child's result under this same parent Task and call mark_task_status with the actual outcome. "
            "Respect pending questions, approvals, stop, deadline and whole-execution usage limits.")
        message = await append_message(db, parent.room_id, None, text,
            {"system_origin": CHILD_HANDOFF_ORIGIN, "execution_id": execution.id,
                "execution_event_id": event.id, "input_revision": child.input_revision,
                "parent_task_id": parent.id, "source_task_id": child.id,
                "parent_assignment_request_id": assignment.request_id,
                "parent_assignment_source_message_id": source.id,
                "accepted_result_id": result.id, "mentions": [], "ingest_only": True},
            thread_root_id=assignment.thread_root_id)
        event.message_id = message.id
        event.details = {**event.details, "message_sha256": hashlib.sha256(message.content.encode()).hexdigest()}
        await db.flush()
        _queue_message(db, message)
    await _resume_parent_handoff(db, execution=execution, parent=parent, event=event, message=message)
    return [message] if created else []


async def reconcile_execution(
    db: AsyncSession, task: Task, *, failure_details: dict | None = None
) -> list[Message]:
    """Persist one lead continuation per child outcome with actual evidence."""
    if not task.execution_id:
        return []
    execution = await _execution(db, task.execution_id)
    if (
        task.id == execution.root_task_id
        or execution.status not in ACTIVE_EXECUTION_STATES
        or task.input_revision != execution.input_revision
        or task.status not in {"done", "failed", "blocked"}
    ):
        return []
    if failure_details is not None:
        from anygarden.project_executions.recovery import (
            resume_failure_notice,
            terminal_recovery_details,
        )

        # Internal callers provide identity only; rebuild public details from
        # the exact current durable failure, never supplied notice text.
        failure_details = await terminal_recovery_details(
            db, task=task, request_id=failure_details.get("request_id"),
            attempt_number=failure_details.get("attempt"),
        )
        if failure_details is None:
            return []
        operating_room = await db.get(Room, execution.operating_room_id)
        source = await db.get(Message, execution.source_message_id)
        if (operating_room is None or operating_room.archived_at is not None
            or operating_room.project_id != execution.project_id
            or source is None or source.room_id != execution.operating_room_id):
            _reject("EXECUTION_SOURCE_UNAVAILABLE", "Execution operating room or source request is unavailable")
    result = await db.scalar(
        select(TaskResult).where(
            TaskResult.task_id == task.id, TaskResult.version == task.result_version
        )
    )
    if task.status == "done" and result is None:
        _reject(
            "COMPLETED_RESULT_MISSING",
            "Execution task cannot complete without its accepted result",
        )
    repair = None
    if (task.role == "qa" and task.status == "done" and result is not None
        and (result.verification or {}).get("verdict") == "fail"):
        from anygarden.project_executions.qa_repairs import (
            advance_pending_repairs,
            repair_savepoint,
            reserve_qa_repair,
        )

        try:
            repair = await reserve_qa_repair(db, qa_task_id=task.id)
        except ExecutionConflict as exc:
            # Nested rollback expires touched ORM rows. Reload explicitly
            # before constructing the durable notice from accepted evidence.
            await db.refresh(execution)
            await db.refresh(task)
            await db.refresh(result)
            # The accepted failure remains intact when no authorized budget
            # or valid repair target exists. Never turn that into a QA pass.
            repair = {"phase": "not_started", "reason_code": exc.code}
            await _event(
                db, execution, f"execution:{execution.id}:qa_repair_denied:{result.id}",
                "qa_repair_not_started", task_id=task.id,
                details={"qa_result_id": result.id, "reason_code": exc.code},
            )
        if repair["phase"] != "not_started":
            try:
                async with repair_savepoint(db):
                    await advance_pending_repairs(db, execution_id=execution.id)
            except ExecutionConflict as exc:
                repair = {**repair, "phase": "waiting", "reason_code": exc.code}
                await db.refresh(execution)
                await db.refresh(task)
                await db.refresh(result)
    event_key = (
        f"turn:{failure_details['request_id']}:attempt:{failure_details['attempt']}:recovery-report"
        if failure_details else
        f"execution:{execution.id}:outcome:{task.id}:r{task.input_revision}:v{task.result_version}:{task.status}"
    )
    event = await _event(
        db,
        execution,
        event_key,
        "task_recovery_reported" if failure_details else f"task_{task.status}",
        task_id=task.id,
        details={
            "status": task.status,
            "result_id": result.id if result and not failure_details else None,
            "result_version": task.result_version,
            "error": failure_details["reason_code"] if failure_details else task.error,
            **({"recovery": failure_details,
                "prior_accepted_result_id": result.id if result else None} if failure_details else {}),
        },
    )
    if event is None:
        if failure_details:
            report = await db.scalar(select(ProjectExecutionEvent).where(
                ProjectExecutionEvent.execution_id == execution.id,
                ProjectExecutionEvent.task_id == task.id,
                ProjectExecutionEvent.event_key == event_key,
                ProjectExecutionEvent.event_type == "task_recovery_reported",
            ))
            message = await db.get(Message, report.message_id) if report and report.message_id else None
            if (message is None or message.room_id != execution.operating_room_id
                or message.root_message_id != (source.root_message_id or source.id)
                or (message.extra_metadata or {}).get("execution_id") != execution.id
                or (message.extra_metadata or {}).get("source_task_id") != task.id):
                _reject("EXECUTION_RECOVERY_NOTICE_INVALID", "Recorded failure notice is not bound to this request")
            await resume_failure_notice(db, execution=execution, task=task,
                report=report, message=message, failure_details=failure_details)
        return [] if failure_details else await _handoff_child_result(
            db, execution=execution, child=task, result=result,
        )
    root = await db.get(Task, execution.root_task_id)
    participant = (
        await db.get(Participant, root.assignee_participant_id) if root else None
    )
    if not failure_details and (
        root is None
        or root.status != "in_progress"
        or participant is None
        or participant.room_id != execution.operating_room_id
        or participant.agent_id != execution.lead_agent_id
    ):
        _reject(
            "EXECUTION_LEAD_UNAVAILABLE",
            "Execution lead task or participant is no longer available",
        )
    source = await db.get(Message, execution.source_message_id)
    text = (
        ("" if failure_details else f"<@user:{participant.id}> ")
        + f"Execution {execution.id}: delegated task {task.id} "
        f"({task.title}) is {task.status}.\nInput revision: {task.input_revision}.\n"
    )
    if failure_details:
        text += (
            "This is a final worker recovery notice, not a completed worker result.\n"
            f"Reason: {failure_details['reason_code']}. Recovery phase: {failure_details['phase']}.\n"
            f"Attempts for the same request: {failure_details['attempt_count']}; "
            f"retries consumed: {failure_details['retry_count']}/{failure_details['max_retries']}.\n"
            f"Last confirmed task stage: {failure_details['last_confirmed_stage']}; "
            f"native started: {failure_details['native_started']}; "
            f"native stop confirmed: {failure_details['native_stop_confirmed']}.\n"
            "No result was accepted from this failed request.\n"
            f"Required action: {failure_details['next_action']}.\n"
        )
        if failure_details["phase"] == "exhausted":
            text += "The worker's retry limit is exhausted. Do not create a new task to retry the same work.\n"
        else:
            text += (
                "Do not retry this worker from the lead turn. An authorized user must resolve "
                "the cause and use the same-task retry gate when eligible.\n"
            )
        text += (
            "Report the failure and required action in this source thread. Preserve results from "
            "existing independent branches, the required plan and failure history. Do not omit "
            "this required task, mark this failure as success, or claim that unknown native effects stopped.\n"
        )
    if result is not None and not failure_details:
        text += (
            f"Accepted result {result.id}, version {result.version}, SHA-256 {result.result_sha256}:\n"
            f"{result.result_markdown}\n"
        )
        if result.verification:
            text += f"QA verification: {result.verification}\n"
    if task.error and not failure_details:
        text += f"Unresolved reason: {task.error}\n"
    if repair is not None:
        text += f"Automatic QA repair state: {repair}\n"
        if repair["phase"] != "not_started":
            text += (
                "The server has reserved the same-task repair and its exact independent re-QA. "
                "Do not delegate a duplicate repair or review. Preserve the failed QA history "
                "and include the reserved re-QA in the required plan.\n"
            )
    text += (
        "Continue the execution from this recorded evidence. Seal the required task plan and call "
        "complete_project_execution with the final user-facing summary only when every required result and QA gate passes. "
        "A plain root mark_task_status(done) cannot complete this execution."
    )
    message = await append_message(
        db,
        execution.operating_room_id,
        None,
        text,
        {
            "system_origin": "project_execution_response" if failure_details else "project_execution",
            "execution_id": execution.id,
            "execution_event_id": event.id,
            "source_task_id": task.id,
            "mentions": [] if failure_details else [{"type": "user", "id": participant.id}],
            "accepted_result_id": result.id if result and not failure_details else None,
            **({"recovery": failure_details, "ingest_only": True} if failure_details else {}),
        },
        thread_root_id=source.root_message_id or source.id,
    )
    event.message_id = message.id
    _queue_message(db, message)
    if failure_details:
        await resume_failure_notice(db, execution=execution, task=task,
            report=event, message=message, failure_details=failure_details)
    else:
        await create_turn(
            db, room_id=execution.operating_room_id, participant_id=participant.id,
            agent_id=execution.lead_agent_id, trigger_message_id=message.id,
            thread_root_id=message.root_message_id, task_id=root.id,
            idempotency_key=event_key,
        )
    await db.execute(
        update(ProjectExecution)
        .where(ProjectExecution.id == execution.id)
        .values(state_revision=ProjectExecution.state_revision + 1, updated_at=_now())
    )
    await db.flush()
    handoffs = [] if failure_details else await _handoff_child_result(
        db, execution=execution, child=task, result=result,
    )
    return [message, *handoffs]


async def seal_plan(
    db: AsyncSession,
    *,
    agent_id: str,
    proof: TurnProof,
    execution_id: str,
    required_task_ids: list[str],
) -> ProjectExecution:
    execution, _ = await _root_turn(
        db, agent_id=agent_id, proof=proof, execution_id=execution_id
    )
    _active(execution)
    root = await db.get(Task, execution.root_task_id)
    if root is None:
        _reject("ROOT_TASK_NOT_ACTIVE", "Execution lead task is unavailable")
    await assert_task_workflow_ready(db, task=root, proof=proof, operation="plan")
    if root.status != "in_progress":
        _reject("ROOT_TASK_NOT_ACTIVE", "Only the claimed lead task can seal its plan")
    ids = list(dict.fromkeys(required_task_ids))
    if not ids or execution.root_task_id in ids:
        _reject("REQUIRED_PLAN_EMPTY", "Seal at least one concrete required child task")
    tasks = list(
        (await db.scalars(select(Task).where(Task.execution_id == execution.id))).all()
    )
    by_id = {task.id: task for task in tasks}
    if any(
        task_id not in by_id
        or by_id[task_id].input_revision != execution.input_revision
        for task_id in ids
    ):
        _reject(
            "REQUIRED_PLAN_INVALID",
            "Every required task must belong to this execution's current input revision",
        )
    mandatory = {
        task.id
        for task in tasks
        if task.id != execution.root_task_id
        and task.input_revision == execution.input_revision
        and task.required_for_execution
    }
    from anygarden.project_executions.qa_repairs import completed_qa_supersessions

    supersessions = await completed_qa_supersessions(db, execution_id=execution.id)
    effective_mandatory = {supersessions.get(task_id, task_id) for task_id in mandatory}
    effective_ids = {supersessions.get(task_id, task_id) for task_id in ids}
    if not effective_mandatory.issubset(effective_ids):
        _reject(
            "REQUIRED_TASK_DROPPED",
            "The plan cannot omit previously declared required work",
        )
    if execution.requires_qa and not any(
        by_id[task_id].role == "qa" for task_id in ids
    ):
        _reject(
            "REQUIRED_QA_MISSING",
            "This execution requires an independent QA task in its completion plan",
        )
    changed = await db.execute(
        update(ProjectExecution)
        .where(
            ProjectExecution.id == execution.id,
            ProjectExecution.status.in_(ACTIVE_EXECUTION_STATES),
            ProjectExecution.input_revision == execution.input_revision,
            ProjectExecution.state_revision == execution.state_revision,
        )
        .values(
            required_task_ids=ids,
            plan_sealed=True,
            status="waiting_children",
            state_revision=ProjectExecution.state_revision + 1,
            updated_at=_now(),
        )
        .returning(ProjectExecution.id)
    )
    if changed.scalar_one_or_none() is None:
        _reject(
            "EXECUTION_PLAN_CONFLICT",
            "Execution changed before its required plan was sealed",
        )
    await db.refresh(execution)
    await _event(
        db,
        execution,
        f"execution:{execution.id}:plan:{execution.state_revision}",
        "plan_sealed",
        task_id=execution.root_task_id,
        details={"required_task_ids": ids, "input_revision": execution.input_revision},
    )
    return execution


async def finish_execution(
    db: AsyncSession,
    *,
    agent_id: str,
    proof: TurnProof,
    execution_id: str,
    summary: str,
) -> ProjectExecution:
    execution, turn = await _root_turn(
        db, agent_id=agent_id, proof=proof, execution_id=execution_id
    )
    if execution.status == "completed":
        return execution
    _active(execution)
    if not execution.plan_sealed or not execution.required_task_ids:
        _reject(
            "EXECUTION_PLAN_NOT_SEALED",
            "Seal concrete required work before completing the execution",
        )
    if not summary.strip():
        _reject("EXECUTION_SUMMARY_REQUIRED", "A final user-facing summary is required")
    await _assert_execution_approvals(db, execution)
    from anygarden.project_executions.qa_repairs import effective_required_task_ids

    effective_ids = await effective_required_task_ids(
        db, execution_id=execution.id, task_ids=execution.required_task_ids,
    )
    required = []
    accepted = {}
    for task_id in effective_ids:
        task = await db.get(Task, task_id)
        result = (
            await db.scalar(
                select(TaskResult).where(
                    TaskResult.task_id == task_id,
                    TaskResult.version == task.result_version,
                )
            )
            if task
            else None
        )
        if (
            task is None
            or task.execution_id != execution.id
            or task.status != "done"
            or task.input_revision != execution.input_revision
            or result is None
            or result.input_revision != execution.input_revision
            or await _pending_requests(db, task)
        ):
            _reject(
                "REQUIRED_TASK_UNRESOLVED",
                f"Required task {task_id} has no accepted successful current result",
            )
        await assert_task_workflow_ready(db, task=task, operation="result")
        required.append(task)
        accepted[task.id] = result
    for task in required:
        if task.role != "qa":
            continue
        result = accepted[task.id]
        target = await db.get(Task, task.qa_target_task_id)
        target_result = await db.scalar(
            select(TaskResult).where(
                TaskResult.task_id == task.qa_target_task_id,
                TaskResult.version == task.qa_target_result_version,
            )
        )
        evidence = result.verification or {}
        if (
            target is None
            or target_result is None
            or target.status != "done"
            or target.result_version != task.qa_target_result_version
            or target_result.input_revision != execution.input_revision
            or target_result.producer_agent_id == result.producer_agent_id
            or evidence.get("verdict") != "pass"
            or evidence.get("target_task_id") != target.id
            or type(evidence.get("target_result_version")) is not int
            or evidence.get("target_result_version") != target.result_version
        ):
            _reject(
                "QA_GATE_NOT_PASSED",
                "Independent QA must pass against the exact current target result",
            )
    root = await db.get(Task, execution.root_task_id)
    if await _pending_requests(db, root):
        _reject(
            "TASK_WAITING_FOR_USER",
            "Lead questions must be answered before final completion",
        )
    await finalize_task_result(
        db,
        root,
        summary,
        agent_id=agent_id,
        proof=proof,
        _allow_root=True,
        verification={
            "required_result_ids": [result.id for result in accepted.values()]
        },
    )
    root = await transition_task_status_cas(
        db, task=root, target_status="done", participant_id=turn.target_participant_id
    )
    source = await db.get(Message, execution.source_message_id)
    message = await append_message(
        db,
        execution.operating_room_id,
        turn.target_participant_id,
        summary,
        {
            "execution_id": execution.id,
            "execution_completed": True,
            "source_message_id": source.id,
            "root_task_id": root.id,
            "required_result_ids": [result.id for result in accepted.values()],
        },
        thread_root_id=source.root_message_id or source.id,
    )
    _queue_message(db, message)
    changed = await db.execute(
        update(ProjectExecution)
        .where(
            ProjectExecution.id == execution.id,
            ProjectExecution.status.in_(ACTIVE_EXECUTION_STATES),
            ProjectExecution.plan_sealed.is_(True),
            ProjectExecution.input_revision == execution.input_revision,
            ProjectExecution.state_revision == execution.state_revision,
        )
        .values(
            status="completed",
            finished_at=_now(),
            updated_at=_now(),
            final_report_message_id=message.id,
            error=None,
            state_revision=ProjectExecution.state_revision + 1,
        )
        .returning(ProjectExecution.id)
    )
    if changed.scalar_one_or_none() is None:
        _reject("EXECUTION_FINISH_CONFLICT", "Execution changed while completing")
    if root.goal_id:
        from anygarden.goals.executor import apply_completion

        await apply_completion(db, root, final_status="done")
    await _event(
        db,
        execution,
        f"execution:{execution.id}:completed",
        "execution_completed",
        task_id=root.id,
        details={
            "final_report_message_id": message.id,
            "required_result_ids": [result.id for result in accepted.values()],
        },
    )
    # Queued lead wakes become obsolete after the authoritative final report.
    from anygarden.db.models import AgentTurnOutbox
    from anygarden.turns.service import mark_turn_terminal

    queued = list(
        (
            await db.scalars(
                select(AgentTurn).where(
                    AgentTurn.task_id == root.id,
                    AgentTurn.request_id != turn.request_id,
                    AgentTurn.state.in_({"pending", "retrying"}),
                )
            )
        ).all()
    )
    for pending in queued:
        await mark_turn_terminal(
            db, pending, state="cancelled", reason="execution_completed", at=_now()
        )
        await db.execute(
            update(AgentTurnOutbox)
            .where(
                AgentTurnOutbox.turn_id == pending.request_id,
                AgentTurnOutbox.state.in_({"pending", "delivering"}),
            )
            .values(state="cancelled")
        )
    await db.flush()
    await db.refresh(execution)
    return execution


def _as_dict(row: Any) -> dict:
    values = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        values[column.name] = (
            value.isoformat() if isinstance(value, datetime) else value
        )
    return values


async def get_bound_task_execution_detail(
    db: AsyncSession, *, execution_id: str, task_id: str
) -> dict:
    """Read only a proved worker's task and its accepted prerequisite inputs.

    The caller must first authorize the invocation and supply its exact
    ``AgentTurn.task_id``. This view never infers a recent turn or grants access
    to other tasks merely because they share a project or execution.
    """
    from sqlalchemy.orm import aliased

    from anygarden.db.execution_approval_models import ExecutionApproval
    from anygarden.project_executions.approvals import approval_payload

    execution = await _execution(db, execution_id)
    task = await db.get(Task, task_id)
    room = await db.get(Room, task.room_id) if task else None
    ops = await db.get(Room, execution.operating_room_id)
    if (
        task is None
        or task.execution_id != execution.id
        or task.input_revision != execution.input_revision
        or room is None
        or room.project_id != execution.project_id
        or ops is None
        or ops.project_id != execution.project_id
    ):
        _reject(
            "BOUND_TASK_SCOPE_INVALID",
            "Read only the invocation's task at its current execution input revision",
        )
    sources = {}
    accepted = {}
    dependencies = []
    for snapshot in task.dependency_results or []:
        if not isinstance(snapshot, dict) or not snapshot.get("task_id"):
            _reject("DEPENDENCY_RESULT_PROOF_INVALID", "Prerequisite snapshot is invalid")
        source = await db.get(Task, snapshot["task_id"])
        source_room = await db.get(Room, source.room_id) if source else None
        if (
            source is None
            or source.id == task.id
            or source.execution_id != execution.id
            or source.input_revision != execution.input_revision
            or source.status != "done"
            or source_room is None
            or source_room.project_id != execution.project_id
            or snapshot.get("execution_id", execution.id) != execution.id
            or snapshot.get("input_revision", execution.input_revision) != execution.input_revision
            or snapshot.get("room_id", source.room_id) != source.room_id
        ):
            _reject(
                "DEPENDENCY_RESULT_SCOPE_INVALID",
                "Prerequisite must be a successful current input of this execution",
            )
        result = await db.scalar(
            select(TaskResult).where(
                TaskResult.task_id == source.id,
                TaskResult.execution_id == execution.id,
                TaskResult.input_revision == execution.input_revision,
                TaskResult.version == source.result_version,
            )
        )
        digest = snapshot.get("result_sha256")
        frozen_body = snapshot.get("result_markdown")
        if (
            result is None
            or not isinstance(digest, str)
            or result.result_sha256 != digest
            or hashlib.sha256(result.result_markdown.encode("utf-8")).hexdigest() != digest
            or (frozen_body is not None and (
                not isinstance(frozen_body, str)
                or hashlib.sha256(frozen_body.encode("utf-8")).hexdigest() != digest
            ))
            or (snapshot.get("result_id") is not None and snapshot["result_id"] != result.id)
            or (snapshot.get("result_version") is not None and (
                type(snapshot["result_version"]) is not int
                or snapshot["result_version"] != result.version
            ))
        ):
            _reject(
                "DEPENDENCY_RESULT_STALE",
                "The frozen prerequisite does not match its latest accepted result version",
            )
        if snapshot.get("result_id") is None and snapshot.get("result_version") is None:
            # Before numeric handoffs existed, the server froze the result body
            # and digest. Resolve that legacy proof only when its immutable
            # accepted version is unique; never silently choose a newer version.
            matches = list(await db.scalars(select(TaskResult.id).where(
                TaskResult.task_id == source.id,
                TaskResult.execution_id == execution.id,
                TaskResult.input_revision == execution.input_revision,
                TaskResult.result_sha256 == digest,
            ).limit(2)))
            if len(matches) != 1:
                _reject(
                    "DEPENDENCY_RESULT_VERSION_AMBIGUOUS",
                    "Prerequisite content must identify one immutable accepted version",
                )
        await _validate_artifacts(db, execution, source, list(result.artifacts or []))
        if source.role == "qa" and (result.verification or {}).get("verdict") != "pass":
            _reject(
                "DEPENDENCY_QA_NOT_PASSED",
                "A failed review is preserved evidence, not a successful prerequisite",
            )
        sources[source.id] = source
        accepted[result.id] = result
        dependencies.append({
            "task_id": source.id,
            "title": source.title,
            "room_id": source.room_id,
            "execution_id": execution.id,
            "input_revision": result.input_revision,
            "result_id": result.id,
            "result_version": result.version,
            "result_sha256": result.result_sha256,
            "result_markdown": result.result_markdown,
            "artifacts": result.artifacts,
        })
    own_results = list(await db.scalars(select(TaskResult).where(
        TaskResult.task_id == task.id,
        TaskResult.execution_id == execution.id,
        TaskResult.input_revision == execution.input_revision,
    ).order_by(TaskResult.version)))
    source_task = aliased(Task)
    source_room = aliased(Room)
    approvals = list(await db.scalars(select(ExecutionApproval)
        .join(source_task, source_task.id == ExecutionApproval.source_task_id)
        .join(source_room, source_room.id == source_task.room_id)
        .join(TaskResult, (TaskResult.id == ExecutionApproval.source_result_id)
            & (TaskResult.task_id == source_task.id))
        .join(RoomArtifact, (RoomArtifact.id == ExecutionApproval.artifact_id)
            & (RoomArtifact.room_id == ExecutionApproval.artifact_room_id))
        .join(Room, Room.id == RoomArtifact.room_id)
        .where(
            ExecutionApproval.execution_id == execution.id,
            ExecutionApproval.task_id == task.id,
            ExecutionApproval.input_revision == execution.input_revision,
            source_task.execution_id == execution.id,
            source_task.input_revision == execution.input_revision,
            TaskResult.execution_id == execution.id,
            TaskResult.input_revision == execution.input_revision,
            source_room.project_id == execution.project_id,
            Room.project_id == execution.project_id,
        ).order_by(ExecutionApproval.created_at, ExecutionApproval.id)))
    own = _as_dict(task)
    own["dependency_results"] = dependencies
    child_results = await get_bound_direct_child_results(db, execution=execution, task=task)
    child_tasks = []
    for child_input in child_results:
        if child_input["task_id"] in sources:
            continue
        child = await db.get(Task, child_input["task_id"])
        child_tasks.append({key: getattr(child, key) for key in (
            "id", "title", "room_id", "parent_task_id", "execution_id",
            "input_revision", "status", "result_version", "role",
        )})
    return {
        "view_scope": "bound_task",
        "own_task_id": task.id,
        "execution": {
            **{key: getattr(execution, key) for key in (
                "id", "project_id", "operating_room_id", "source_message_id",
                "input_revision", "status", "objective", "allowed_actions", "limits",
                "native_invocations_reserved", "usage_summary",
            )},
            "delegation_count": await db.scalar(select(delegation_total_expression(execution.id))),
        },
        "tasks": [own, *[
            {key: getattr(source, key) for key in (
                "id", "title", "room_id", "execution_id", "input_revision",
                "status", "result_version",
            )} for source in sources.values()
        ], *child_tasks],
        "results": [_as_dict(result) for result in [*own_results, *accepted.values()]],
        "dependency_results": dependencies,
        "child_results": child_results,
        "approvals": [approval_payload(row) for row in approvals],
        "enforcement": {"scope": "managed_workflow", "native_engine_egress_controlled": False},
    }


async def get_execution_detail(db: AsyncSession, execution_id: str, *, access=None) -> dict:
    """JSON-ready history; project join prevents corrupt cross-project leakage."""
    from anygarden.db.execution_approval_models import ExecutionApproval

    execution = await _execution(db, execution_id)
    tasks = list(
        (
            await db.scalars(
                select(Task)
                .join(Room, Room.id == Task.room_id)
                .where(
                    Task.execution_id == execution.id,
                    Room.project_id == execution.project_id,
                )
                .order_by(Task.created_at, Task.id)
            )
        ).all()
    )
    task_ids = [task.id for task in tasks]
    results = list(
        (
            await db.scalars(
                select(TaskResult)
                .where(
                    TaskResult.execution_id == execution.id,
                    TaskResult.task_id.in_(task_ids),
                )
                .order_by(TaskResult.created_at, TaskResult.id)
            )
        ).all()
    )
    revisions = list(
        (
            await db.scalars(
                select(ExecutionInputRevision)
                .where(
                    ExecutionInputRevision.execution_id == execution.id,
                )
                .order_by(ExecutionInputRevision.revision)
            )
        ).all()
    )
    events = list(
        (
            await db.scalars(
                select(ProjectExecutionEvent)
                .where(
                    ProjectExecutionEvent.execution_id == execution.id,
                    or_(
                        ProjectExecutionEvent.task_id.is_(None),
                        ProjectExecutionEvent.task_id.in_(task_ids),
                    ),
                )
                .order_by(ProjectExecutionEvent.created_at, ProjectExecutionEvent.id)
            )
        ).all()
    )
    approvals = list(
        (
            await db.scalars(
                select(ExecutionApproval)
                .join(
                    TaskResult,
                    (TaskResult.id == ExecutionApproval.source_result_id)
                    & (TaskResult.task_id == ExecutionApproval.source_task_id),
                )
                .join(
                    RoomArtifact,
                    (RoomArtifact.id == ExecutionApproval.artifact_id)
                    & (RoomArtifact.room_id == ExecutionApproval.artifact_room_id),
                )
                .join(Room, Room.id == RoomArtifact.room_id)
                .where(
                    ExecutionApproval.execution_id == execution.id,
                    ExecutionApproval.task_id.in_(task_ids),
                    ExecutionApproval.source_task_id.in_(task_ids),
                    TaskResult.execution_id == execution.id,
                    Room.project_id == execution.project_id,
                )
                .order_by(ExecutionApproval.created_at, ExecutionApproval.id)
            )
        ).all()
    )
    from anygarden.project_executions.approvals import approval_payload
    from anygarden.project_executions.serialization import enrich_execution_detail

    detail = {
        "execution": _as_dict(execution),
        "input_revisions": [_as_dict(row) for row in revisions],
        "tasks": [_as_dict(row) for row in tasks],
        "results": [_as_dict(row) for row in results],
        "events": [_as_dict(row) for row in events],
        "approvals": [approval_payload(row) for row in approvals],
        "enforcement": {
            "scope": "managed_workflow",
            "native_engine_egress_controlled": False,
        },
    }
    return await enrich_execution_detail(db, detail, execution=execution, access=access)
