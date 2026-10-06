"""Current-task recovery hooks and a safe, permission-scoped retry ledger.

Native cleanup is proved by the durable terminal event written by the turn
service. This module never infers process termination from an expired lease,
changes an accepted result, or creates a replacement task/request.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import String, exists, func, or_, select, update
from sqlalchemy.orm import aliased

from anygarden.db.execution_approval_models import ExecutionApproval
from anygarden.db.execution_request_models import ExecutionRequest
from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    AgentTurnOutbox,
    ExecutionStop,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    Task,
    TaskBlocker,
    TaskResult,
)
from anygarden.project_executions.authorization import ACTIVE_STATES, SAFE_STOP_STATES

PUBLIC_REASONS = frozenset({
    "MODEL_CALL_FAILED", "MODEL_TIMEOUT", "MODEL_CONFIGURATION_INVALID", "RATE_LIMITED",
    "QUOTA_EXHAUSTED", "PROCESS_LOST", "PROCESS_OUTCOME_UNKNOWN", "LEASE_EXPIRED",
    "AGENT_STOPPED", "TRANSPORT_UNAVAILABLE", "NATIVE_STOP_UNCONFIRMED",
    "AUTHORIZATION_REVOKED", "RETRY_EXHAUSTED", "INPUT_SUPERSEDED", "EXECUTION_CANCELLED",
    "EXECUTION_DEADLINE_REACHED", "FAILED_REPLY", "LEGACY_RETRY_UNSUPPORTED",
    "RECOVERY_PENDING", "UNKNOWN_FAILURE", "AUTHENTICATION_FAILED", "MODEL_TRANSIENT_FAILURE", "MODEL_EXECUTION_FAILED", "TASK_BLOCKED",
    "EXECUTION_NATIVE_INVOCATION_LIMIT", "EXECUTION_USAGE_LIMIT_REACHED",
    "EXECUTION_USAGE_ACCOUNTING_INCOMPLETE",
    "APPROVAL_REJECTED", "QA_VERIFICATION_FAILED",
})
log = logging.getLogger(__name__)
NATIVE_REASONS = frozenset({"MODEL_CONFIGURATION_INVALID", "AUTHENTICATION_FAILED",
    "MODEL_TRANSIENT_FAILURE", "MODEL_EXECUTION_FAILED", "PROCESS_OUTCOME_UNKNOWN"})
_REASON_ALIASES = {
    "ENGINE_ERROR": "MODEL_CALL_FAILED", "PI_PROVIDER_ERROR": "MODEL_CALL_FAILED",
    "TIMEOUT_STOPPED": "MODEL_TIMEOUT", "ENGINE_AUTH_ERROR": "MODEL_CONFIGURATION_INVALID",
    "AUTH_MISSING": "MODEL_CONFIGURATION_INVALID", "AUTH_CHECK_FAILED": "MODEL_CONFIGURATION_INVALID",
    "UNKNOWN_PROVIDER": "MODEL_CONFIGURATION_INVALID", "UNSUPPORTED_RUNTIME": "MODEL_CONFIGURATION_INVALID",
    "POLICY_DENIED": "AUTHORIZATION_REVOKED", "process_lost": "PROCESS_LOST",
    "generation_interrupted": "PROCESS_LOST", "lease_expired": "LEASE_EXPIRED",
    "agent_not_running": "AGENT_STOPPED", "agent_cancelled": "AGENT_STOPPED",
    "authorization_revoked": "AUTHORIZATION_REVOKED", "retry_exhausted": "RETRY_EXHAUSTED",
    "legacy_interrupted": "LEGACY_RETRY_UNSUPPORTED", "EXECUTION_STOP_UNCONFIRMED": "NATIVE_STOP_UNCONFIRMED",
    "EXECUTION_INPUT_SUPERSEDED": "INPUT_SUPERSEDED", "execution_input_superseded": "INPUT_SUPERSEDED",
    "execution_cancelled": "EXECUTION_CANCELLED", "EXECUTION_DEADLINE_REACHED": "EXECUTION_DEADLINE_REACHED",
    "agent_failed_without_completion": "MODEL_CALL_FAILED", "agent_timeout_without_completion": "MODEL_TIMEOUT",
    "사용자가 실행 승인을 거절했습니다. 전송하지 않습니다.": "APPROVAL_REJECTED",
}
_ATTEMPT_STATES = frozenset({"pending", "leased", "started", "completing", "completed", "cancelled", "failed", "interrupted", "stale"})
_OUTCOMES = frozenset({"ok", "succeeded", "failed", "timeout", "cancelled", "interrupted", "skipped", "rejected", "retry_exhausted"})
_OPEN_STATES = frozenset({"pending", "leased", "retrying", "completing"})


def public_reason_code(value: str | None) -> str | None:
    """Only closed codes reach a public task projection; never raw exceptions."""
    if not value:
        return None
    if value in PUBLIC_REASONS:
        return value
    return _REASON_ALIASES.get(value, "UNKNOWN_FAILURE")


def public_task_error(status: str | None, value: str | None) -> str | None:
    """Public error code for a task, aware of a worker's deliberate block.

    A worker that marks its task ``blocked`` explains why in free text. That
    text is not a closed code, but it is not an unknown failure either:
    report it as ``TASK_BLOCKED`` (the explanation stays in the task's
    messages and result) instead of ``UNKNOWN_FAILURE``.
    """
    code = public_reason_code(value)
    if status == "blocked" and code == "UNKNOWN_FAILURE" and value not in PUBLIC_REASONS:
        return "TASK_BLOCKED"
    return code


async def _native_terminal(db, turn, attempt):
    event = await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == turn.execution_id,
        ProjectExecutionEvent.task_id == turn.task_id,
        ProjectExecutionEvent.event_key == f"turn:{turn.request_id}:attempt:{attempt.attempt_number}:native-terminal",
    ))
    details = event.details if event and isinstance(event.details, dict) else {}
    if (not attempt.local_execution_id or details.get("local_execution_id") != attempt.local_execution_id
        or details.get("outcome") != "failed"
        or details.get("process_state") not in {"finished", "stopped", "not_started"}
        or details.get("reason_code") not in NATIVE_REASONS
        or details.get("reason_code") == "PROCESS_OUTCOME_UNKNOWN"):
        return None
    return details


async def _unfinished_repair_intent(db, *, turn, attempt):
    """Only a started reserved repair may retain its immutable prior result."""
    from anygarden.project_executions.qa_repairs import repair_intent_for_turn

    intent = await repair_intent_for_turn(db, turn=turn, attempt=attempt)
    if intent is None:
        return None
    base_version = intent.get("base_result_version")
    if (type(base_version) is not int or base_version < 1
        or type(intent.get("expected_result_version")) is not int
        or intent["expected_result_version"] != base_version + 1
        or intent.get("repair_request_id") != turn.request_id
        or intent.get("target_task_id") != turn.task_id
        or intent.get("input_revision") != turn.execution_input_revision
        or intent.get("producer_agent_id") != turn.agent_id
        or intent.get("assignee_participant_id") != turn.target_participant_id):
        return None
    return intent


def _repair_result_conditions(turn, intent):
    """CAS the exact base and unfinished repair request, never any old result."""
    return (
        Task.result_version == intent["base_result_version"],
        Task.source_message_id == intent["assignment_message_id"],
        exists(select(TaskResult.id).where(
            TaskResult.id == intent["base_result_id"], TaskResult.task_id == Task.id,
            TaskResult.execution_id == turn.execution_id,
            TaskResult.input_revision == turn.execution_input_revision,
            TaskResult.version == intent["base_result_version"],
            TaskResult.result_sha256 == intent["base_result_sha256"],
            TaskResult.producer_agent_id == turn.agent_id,
        )),
        ~exists(select(TaskResult.id).join(AgentTurnAttempt,
            AgentTurnAttempt.id == TaskResult.attempt_id).where(
                TaskResult.task_id == Task.id,
                AgentTurnAttempt.turn_id == turn.request_id,
            )),
        exists(select(AgentTurn.request_id).where(
            AgentTurn.request_id == turn.request_id, AgentTurn.task_id == Task.id,
            AgentTurn.execution_id == turn.execution_id,
            AgentTurn.execution_input_revision == turn.execution_input_revision,
            AgentTurn.agent_id == intent["producer_agent_id"],
            AgentTurn.target_participant_id == intent["assignee_participant_id"],
            AgentTurn.trigger_message_id == intent["assignment_message_id"],
            AgentTurn.accepted_message_id.is_(None),
        )),
    )


async def _retry_denial(db, *, task, execution, turn, attempt) -> str | None:
    if (execution is None or task.execution_id != execution.id or execution.status not in ACTIVE_STATES
        or task.input_revision != execution.input_revision
        or turn is None or attempt is None or turn.execution_id != execution.id
        or turn.execution_input_revision != task.input_revision
        or turn.task_id != task.id or turn.room_id != task.room_id
        or turn.target_participant_id != task.assignee_participant_id
        or attempt.turn_id != turn.request_id or attempt.agent_id != turn.agent_id
        or attempt.attempt_number != turn.active_attempt):
        return "AUTHORIZATION_REVOKED"
    if task.status == "done" or turn.accepted_message_id:
        return "ACCEPTED_RESULT_PRESENT"
    prior_result = task.result_version or await db.scalar(select(TaskResult.id).where(
        TaskResult.task_id == task.id,
    ).limit(1))
    if prior_result and await _unfinished_repair_intent(db, turn=turn, attempt=attempt) is None:
        return "ACCEPTED_RESULT_PRESENT"
    if task.status not in {"blocked", "failed"} or turn.state not in {"failed", "cancelled"}:
        return "RECOVERY_PENDING"
    if attempt.state not in {"failed", "interrupted", "cancelled", "stale"}:
        return "RECOVERY_PENDING"
    if execution.deadline_at is not None and execution.deadline_at <= datetime.now(UTC):
        return "EXECUTION_DEADLINE_REACHED"
    if turn.retry_count >= turn.max_retries:
        return "RETRY_EXHAUSTED"
    native = await _native_terminal(db, turn, attempt)
    if native is None or native.get("retryable") is not True:
        return "PROCESS_OUTCOME_UNKNOWN"
    if await db.scalar(select(AgentTurn.request_id).where(
        AgentTurn.task_id == task.id, AgentTurn.execution_id == execution.id,
        AgentTurn.execution_input_revision == task.input_revision,
        AgentTurn.state.in_(_OPEN_STATES),
    ).limit(1)):
        return "RECOVERY_PENDING"
    if await db.scalar(select(ExecutionStop.id).where(
        ExecutionStop.execution_id == execution.id, ExecutionStop.status.not_in(SAFE_STOP_STATES),
    ).limit(1)):
        return "NATIVE_STOP_UNCONFIRMED"
    if await db.scalar(select(ExecutionRequest.id).where(
        ExecutionRequest.task_id == task.id, ExecutionRequest.execution_id == execution.id,
        ExecutionRequest.input_revision == task.input_revision, ExecutionRequest.status == "pending",
    ).limit(1)):
        return "QUESTION_PENDING"
    if await db.scalar(select(ExecutionApproval.id).where(
        ExecutionApproval.task_id == task.id, ExecutionApproval.execution_id == execution.id,
        ExecutionApproval.input_revision == task.input_revision,
        ExecutionApproval.status != "succeeded",
    ).limit(1)):
        return "APPROVAL_PENDING"
    if await db.scalar(select(TaskBlocker.task_id).where(TaskBlocker.task_id == task.id).limit(1)):
        return "DEPENDENCY_PENDING"
    gate = await db.scalar(select(Agent.id).join(Participant, Participant.agent_id == Agent.id)
        .join(Room, Room.id == Participant.room_id).where(
            Agent.id == turn.agent_id, Agent.desired_state == "running", Agent.actual_state == "running",
            Agent.pending_generation.is_(None),
            Participant.id == turn.target_participant_id, Participant.room_id == task.room_id,
            Participant.role.in_({"member", "admin", "owner"}), Room.project_id == execution.project_id,
            Room.archived_at.is_(None),
        ))
    if not gate:
        return "AGENT_STOPPED"
    from anygarden.project_executions.usage import admission_disposition

    admission = await admission_disposition(db, execution=execution, phase="intent")
    return None if admission.allowed else admission.reason_code or "RECOVERY_PENDING"


async def task_recovery_payload(db, task: Task, *, execution=None, access=None) -> dict | None:
    """Read actual scoped attempts. Caller access controls the retry capability."""
    if not task.execution_id:
        return None
    execution = execution or await db.get(ProjectExecution, task.execution_id)
    room = await db.get(Room, task.room_id)
    if execution is None or execution.id != task.execution_id or room is None or room.project_id != execution.project_id:
        return None
    turns = list(await db.scalars(select(AgentTurn).where(
        AgentTurn.task_id == task.id, AgentTurn.execution_id == execution.id,
        AgentTurn.execution_input_revision == task.input_revision,
    ).order_by(AgentTurn.created_at, AgentTurn.request_id)))
    attempts = list(await db.scalars(select(AgentTurnAttempt).join(AgentTurn, AgentTurn.request_id == AgentTurnAttempt.turn_id)
        .where(AgentTurn.task_id == task.id, AgentTurn.execution_id == execution.id,
            AgentTurn.execution_input_revision == task.input_revision)
        .order_by(AgentTurn.created_at, AgentTurn.request_id, AgentTurnAttempt.attempt_number)))
    turn = turns[-1] if turns else None
    current = next((row for row in attempts if turn and row.turn_id == turn.request_id
        and row.attempt_number == turn.active_attempt), None)
    phase_event = await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution.id, ProjectExecutionEvent.task_id == task.id,
        ProjectExecutionEvent.event_type == "task_recovery",
    ).order_by(ProjectExecutionEvent.created_at.desc(), ProjectExecutionEvent.id).limit(1))
    phase = phase_event.details if phase_event and isinstance(phase_event.details, dict) else {}
    if turn and phase.get("request_id") != turn.request_id:
        phase = {}
    native = await _native_terminal(db, turn, current) if turn and current else None
    reason = public_reason_code(native.get("reason_code") if native else None)
    reason = reason or public_reason_code(phase.get("reason_code") or (current.reason if current else None) or task.error)
    outbox = await db.scalar(select(AgentTurnOutbox).where(
        AgentTurnOutbox.attempt_id == current.id, AgentTurnOutbox.state == "pending",
    )) if current else None
    state, action = "none", "none"
    if task.input_revision != execution.input_revision:
        state, reason = "historical", "INPUT_SUPERSEDED"
    elif execution.error == "EXECUTION_DEADLINE_REACHED" and task.status != "done":
        state = "failed" if execution.status == "failed" else "action_required"
        reason, action = "EXECUTION_DEADLINE_REACHED", "review_task_details"
    elif execution.error in {"EXECUTION_NATIVE_INVOCATION_LIMIT", "EXECUTION_USAGE_LIMIT_REACHED"} and task.status != "done":
        state = "failed" if execution.status == "failed" else "action_required"
        reason, action = execution.error, "review_failure"
    elif execution.status in {"cancelled", "cancelling"}:
        state, reason = "cancelled", "EXECUTION_CANCELLED"
    elif task.status == "done":
        state = "completed"
        if task.role == "qa" and reason in {None, "UNKNOWN_FAILURE"}:
            accepted = await db.scalar(select(TaskResult).where(
                TaskResult.task_id == task.id, TaskResult.version == task.result_version,
                TaskResult.execution_id == execution.id,
                TaskResult.input_revision == task.input_revision,
            ))
            verification = accepted.verification if accepted else None
            if (task.qa_target_task_id is not None
                and type(task.qa_target_result_version) is int and task.qa_target_result_version > 0
                and isinstance(verification, dict) and verification.get("verdict") == "fail"
                and verification.get("target_task_id") == task.qa_target_task_id
                and type(verification.get("target_result_version")) is int
                and verification.get("target_result_version") == task.qa_target_result_version
                and hashlib.sha256(accepted.result_markdown.encode()).hexdigest() == accepted.result_sha256):
                # A completed independent review can reject the artifact's
                # quality without a failed model call or another retry.
                reason, action = "QA_VERIFICATION_FAILED", "review_task_details"
    elif task.status == "blocked" and turn and turn.state == "completed" and execution.status in ACTIVE_STATES:
        # Finishing a native turn does not certify its domain task. Preserve
        # dedicated question/approval/dependency cards rather than inventing
        # a model failure or authorizing another successful native invocation.
        waiting_question = await db.scalar(select(ExecutionRequest.id).where(
            ExecutionRequest.task_id == task.id, ExecutionRequest.execution_id == execution.id,
            ExecutionRequest.input_revision == task.input_revision, ExecutionRequest.status == "pending",
        ).limit(1))
        waiting_approval = await db.scalar(select(ExecutionApproval.id).where(
            ExecutionApproval.task_id == task.id, ExecutionApproval.execution_id == execution.id,
            ExecutionApproval.input_revision == task.input_revision, ExecutionApproval.status != "succeeded",
        ).limit(1))
        waiting_dependency = await db.scalar(select(TaskBlocker.task_id).where(
            TaskBlocker.task_id == task.id,
        ).limit(1))
        if not (waiting_question or waiting_approval or waiting_dependency):
            state, reason, action = "action_required", "TASK_BLOCKED", "review_task_details"
    elif task.status == "failed" or (turn and turn.state == "failed" and turn.retry_count >= turn.max_retries):
        state, action = "failed", "review_failure"
    elif (phase.get("phase") == "waiting_retry" and turn
        and phase.get("attempt") == turn.active_attempt and task.status == "blocked"):
        state, action = "retry_wait", "wait_for_retry"
    elif turn and turn.state in _OPEN_STATES and turn.retry_count:
        if current and current.state == "started":
            state = "running"
        elif outbox and outbox.available_at > datetime.now(UTC):
            state, action = "retry_wait", "wait_for_retry"
        else:
            state, action = "retrying", "wait_for_retry"
    elif phase.get("phase") in {"action_required", "waiting_retry", "exhausted"} and (not turn or turn.state not in _OPEN_STATES):
        state, action = "action_required", "check_agent"
    elif turn and turn.state in _OPEN_STATES:
        state = "running"
    elif turn and turn.state in {"failed", "cancelled"}:
        state, action = "action_required", "check_agent"
    denial = await _retry_denial(db, task=task, execution=execution, turn=turn, attempt=current)
    from anygarden.project_executions.mutations import can_manage_execution

    can_retry = bool(denial is None and access is not None
        and access.room.id == execution.operating_room_id and can_manage_execution(access, execution))
    if denial == "NATIVE_STOP_UNCONFIRMED" or reason == "PROCESS_OUTCOME_UNKNOWN":
        if state not in {"completed", "cancelled", "historical"}:
            state, action = "action_required", "await_safe_stop"
    elif denial == "QUESTION_PENDING":
        action = "answer_question"
    elif denial == "APPROVAL_PENDING":
        action = "review_approval"
    if denial in {"EXECUTION_NATIVE_INVOCATION_LIMIT", "EXECUTION_USAGE_LIMIT_REACHED",
                   "EXECUTION_USAGE_ACCOUNTING_INCOMPLETE"}:
        reason, action = denial, "review_failure"
    if reason == "RETRY_EXHAUSTED":
        action = "review_failure"
    elif state == "action_required" and reason == "MODEL_CONFIGURATION_INVALID":
        action = "fix_configuration_and_retry"
    elif state == "action_required" and reason == "AUTHENTICATION_FAILED":
        action = "restore_authentication_and_retry"
    elif state == "action_required" and can_retry:
        action = "retry_task"
    return {"state": state, "reason_code": reason, "next_action": action, "can_retry": can_retry,
        "request_id": turn.request_id if turn else None, "active_attempt": turn.active_attempt if turn else None,
        "attempt_count": len(attempts), "completed_attempt_count": sum(row.state in {"completed", "cancelled", "failed", "interrupted", "stale"} for row in attempts),
        "retry_count": turn.retry_count if turn else 0, "max_retries": turn.max_retries if turn else 0,
        "next_retry_at": outbox.available_at.isoformat() if outbox and turn and turn.retry_count
            else phase.get("next_retry_at") if state == "retry_wait" else None,
        "attempts": [{"ordinal": ordinal, "state": row.state if row.state in _ATTEMPT_STATES else "unknown",
            "outcome": row.outcome if row.outcome in _OUTCOMES or row.outcome is None else "unknown",
            "reason_code": public_reason_code(row.reason), "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.ended_at.isoformat() if row.ended_at else None} for ordinal, row in enumerate(attempts, 1)]}


async def on_turn_recovery(db, *, turn, attempt, phase: str, reason_code: str | None, next_retry_at=None) -> bool:
    """Project a real producer transition inside the turn service transaction."""
    if phase not in {"action_required", "waiting_retry", "retrying", "exhausted"}:
        raise ValueError("Unknown task recovery phase")
    if (not turn.execution_id or not turn.task_id or attempt.turn_id != turn.request_id
        or attempt.agent_id != turn.agent_id or attempt.attempt_number != turn.active_attempt):
        return False
    # Deliberately gate revision/status without requiring current agent running:
    # a real stopped agent is precisely when this hook must expose waiting.
    locked = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == turn.execution_id,
        ProjectExecution.input_revision == turn.execution_input_revision,
        ProjectExecution.status.in_(ACTIVE_STATES),
    ).values(state_revision=ProjectExecution.state_revision).returning(ProjectExecution.id))
    if locked is None:
        return False
    from anygarden.project_executions.service import _event

    key = f"turn:{turn.request_id}:attempt:{attempt.attempt_number}:recovery:{phase}"
    if await db.scalar(select(ProjectExecutionEvent.id).where(ProjectExecutionEvent.event_key == key)):
        return False
    code = public_reason_code(reason_code)
    values = {"status": "in_progress", "error": None, "finished_at": None} if phase == "retrying" else {
        "status": "failed" if phase == "exhausted" else "blocked", "error": code,
        "finished_at": datetime.now(UTC) if phase == "exhausted" else None,
    }
    intent = await _unfinished_repair_intent(db, turn=turn, attempt=attempt)
    result_conditions = _repair_result_conditions(turn, intent) if intent else (
        Task.result_version == 0,
        ~exists(select(TaskResult.id).where(TaskResult.task_id == Task.id)),
    )
    changed = await db.scalar(update(Task).where(
        Task.id == turn.task_id, Task.execution_id == turn.execution_id,
        Task.input_revision == turn.execution_input_revision, Task.room_id == turn.room_id,
        Task.assignee_participant_id == turn.target_participant_id,
        Task.status.in_({"todo", "in_progress", "blocked", "failed"}),
        *result_conditions,
        exists(select(AgentTurn.request_id).where(AgentTurn.request_id == turn.request_id,
            AgentTurn.task_id == Task.id, AgentTurn.active_attempt == attempt.attempt_number)),
    ).values(**values).returning(Task.id))
    if changed is None:
        return False
    execution = await db.get(ProjectExecution, turn.execution_id)
    await _event(db, execution, key, "task_recovery", task_id=turn.task_id,
        details={"request_id": turn.request_id, "attempt": attempt.attempt_number, "phase": phase,
            "reason_code": code, "next_retry_at": next_retry_at.isoformat() if next_retry_at else None})
    db.info.setdefault("project_execution_recovery_tasks", set()).add(turn.task_id)
    if phase in {"exhausted", "action_required"}:
        await report_terminal_recovery(db, request_id=turn.request_id,
            attempt_number=attempt.attempt_number)
    return True


async def terminal_recovery_details(db, *, task, request_id: str, attempt_number: int) -> dict | None:
    """Describe one recorded final failure, without treating it as a result.

    A later retry/answer, accepted result, revision, cancellation or deadline
    fences the old notice. Unknown native outcomes remain unknown: a lead
    continuation is permission to report the failure, not retry that worker.
    """
    if not task.execution_id or not isinstance(request_id, str) or type(attempt_number) is not int:
        return None
    execution = await db.get(ProjectExecution, task.execution_id)
    turn = await db.get(AgentTurn, request_id)
    attempt = await db.scalar(select(AgentTurnAttempt).where(
        AgentTurnAttempt.turn_id == request_id,
        AgentTurnAttempt.attempt_number == attempt_number,
    ))
    if (execution is None or execution.status not in ACTIVE_STATES
        or task.id == execution.root_task_id or task.input_revision != execution.input_revision
        or task.status not in {"blocked", "failed"}
        or (execution.deadline_at is not None and execution.deadline_at <= datetime.now(UTC))
        or turn is None or attempt is None or turn.state != "failed"
        or turn.execution_id != execution.id or turn.task_id != task.id
        or turn.execution_input_revision != task.input_revision or turn.room_id != task.room_id
        or turn.target_participant_id != task.assignee_participant_id
        or task.source_message_id != turn.trigger_message_id
        or turn.accepted_message_id is not None or turn.active_attempt != attempt_number
        or attempt.agent_id != turn.agent_id
        or attempt.state not in {"failed", "interrupted", "cancelled", "stale"}):
        return None
    room = await db.get(Room, task.room_id)
    participant = await db.get(Participant, task.assignee_participant_id)
    if (room is None or room.project_id != execution.project_id or room.archived_at is not None
        or participant is None or participant.room_id != task.room_id
        or participant.agent_id != turn.agent_id or participant.role not in {"owner", "admin", "member"}):
        return None
    if await db.scalar(select(AgentTurn.request_id).where(
        AgentTurn.task_id == task.id, AgentTurn.execution_id == execution.id,
        AgentTurn.execution_input_revision == task.input_revision,
        AgentTurn.state.in_(_OPEN_STATES),
    ).limit(1)):
        return None
    # Every attempt of this request is checked. An immutable base belonging to
    # an earlier producer request is allowed only by the reserved repair gate.
    if await db.scalar(select(TaskResult.id).join(AgentTurnAttempt,
        AgentTurnAttempt.id == TaskResult.attempt_id).where(
            TaskResult.task_id == task.id, AgentTurnAttempt.turn_id == request_id,
        ).limit(1)):
        return None
    if (task.result_version or await db.scalar(select(TaskResult.id).where(
        TaskResult.task_id == task.id,
    ).limit(1))) and await _unfinished_repair_intent(db, turn=turn, attempt=attempt) is None:
        return None
    phase_event = await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution.id,
        ProjectExecutionEvent.task_id == task.id,
        ProjectExecutionEvent.event_type == "task_recovery",
        ProjectExecutionEvent.event_key.in_([
            f"turn:{request_id}:attempt:{attempt_number}:recovery:exhausted",
            f"turn:{request_id}:attempt:{attempt_number}:recovery:action_required",
        ]),
    ).order_by(ProjectExecutionEvent.created_at.desc(), ProjectExecutionEvent.id).limit(1))
    phase = phase_event.details if phase_event and isinstance(phase_event.details, dict) else {}
    if (phase.get("request_id") != request_id or type(phase.get("attempt")) is not int
        or phase["attempt"] != attempt_number
        or phase.get("phase") not in {"exhausted", "action_required"}):
        return None
    native = await _native_terminal(db, turn, attempt)
    reason = public_reason_code(phase.get("reason_code")) or "UNKNOWN_FAILURE"
    action = "review_failure" if phase["phase"] == "exhausted" else {
        "MODEL_CONFIGURATION_INVALID": "fix_configuration_and_retry",
        "AUTHENTICATION_FAILED": "restore_authentication_and_retry",
        "PROCESS_OUTCOME_UNKNOWN": "await_safe_stop",
        "NATIVE_STOP_UNCONFIRMED": "await_safe_stop",
    }.get(reason, "check_agent")
    count = await db.scalar(select(func.count()).select_from(AgentTurnAttempt).where(
        AgentTurnAttempt.turn_id == request_id,
    ))
    return {"request_id": request_id, "attempt": attempt_number,
        "recovery_event_id": phase_event.id, "phase": phase["phase"],
        "reason_code": reason, "next_action": action,
        "attempt_count": count, "retry_count": turn.retry_count, "max_retries": turn.max_retries,
        "input_revision": task.input_revision, "source_message_id": execution.source_message_id,
        "assignment_message_id": turn.trigger_message_id,
        "accepted_result_for_request": False,
        "last_confirmed_stage": "delegated",
        "native_started": attempt.started_at is not None,
        "native_stop_confirmed": native is not None}


async def resume_failure_notice(db, *, execution, task, report, message, failure_details) -> bool:
    """A human notice is durable independently of this once-only lead intent.

    Native/permission gates may deny a continuation. Its inner savepoint then
    drops only the new wake/outbox, leaving the truthful report for the user.
    No worker task/request/result is retried or changed here.
    """
    from anygarden.project_executions.qa_repairs import repair_savepoint
    from anygarden.project_executions.service import ExecutionConflict, _event, _reject
    from anygarden.rooms.authorization import AGENT_EXECUTION_ROLES
    from anygarden.turns.service import OPEN_TURN_STATES, create_turn

    execution_id, task_id = execution.id, task.id
    report_id, message_id = report.id, message.id
    recorded = (report.details or {}).get("recovery", {})
    if (not isinstance(recorded, dict) or report.execution_id != execution_id
        or report.task_id != task_id or report.event_type != "task_recovery_reported"
        or report.message_id != message_id or message.room_id != execution.operating_room_id
        or any(recorded.get(key) != failure_details.get(key) for key in (
            "request_id", "attempt", "input_revision", "source_message_id", "assignment_message_id",
        ))):
        _reject("EXECUTION_RECOVERY_NOTICE_INVALID", "Failure notice identity does not match its current task")
    wake_key = f"turn:{failure_details['request_id']}:attempt:{failure_details['attempt']}:recovery-wake"
    if await db.scalar(select(ProjectExecutionEvent.id).where(
        ProjectExecutionEvent.execution_id == execution_id,
        ProjectExecutionEvent.task_id == task_id,
        ProjectExecutionEvent.event_key == wake_key,
        ProjectExecutionEvent.event_type == "task_recovery_lead_wake",
    )):
        return False
    try:
        async with repair_savepoint(db):
            root = await db.get(Task, execution.root_task_id)
            # Keep the original notice idempotency key as the Turn key. An
            # already-created continuation from the previous reporter is
            # adopted as history, never run again under a new key/budget.
            continuation = await db.scalar(select(AgentTurn).where(
                AgentTurn.idempotency_key == report.event_key,
            ))
            if continuation is not None:
                if (root is None or continuation.task_id != root.id
                    or continuation.execution_id != execution_id
                    or continuation.execution_input_revision != task.input_revision
                    or continuation.room_id != execution.operating_room_id
                    or continuation.agent_id != execution.lead_agent_id
                    or continuation.target_participant_id != root.assignee_participant_id
                    or continuation.trigger_message_id != message_id):
                    _reject("EXECUTION_LEAD_UNAVAILABLE", "Existing lead intent has a different binding")
            else:
                participant = await db.get(Participant, root.assignee_participant_id) if root else None
                lead = await db.get(Agent, execution.lead_agent_id)
                if (root is None or root.status != "in_progress"
                    or root.execution_id != execution_id or root.input_revision != execution.input_revision
                    or root.room_id != execution.operating_room_id
                    or participant is None or participant.room_id != execution.operating_room_id
                    or participant.agent_id != execution.lead_agent_id
                    or participant.role not in AGENT_EXECUTION_ROLES
                    or lead is None or lead.desired_state != "running"):
                    _reject("EXECUTION_LEAD_UNAVAILABLE", "Execution lead cannot currently resume")
                continuation = await create_turn(
                    db, room_id=execution.operating_room_id, participant_id=participant.id,
                    agent_id=execution.lead_agent_id, trigger_message_id=message_id,
                    thread_root_id=message.root_message_id, task_id=root.id,
                    idempotency_key=report.event_key,
                )
                if continuation.state not in OPEN_TURN_STATES:
                    _reject("EXECUTION_LEAD_UNAVAILABLE", "Execution lead continuation is currently fenced")
            event = await _event(db, execution, wake_key, "task_recovery_lead_wake", task_id=task_id,
                details={"recovery_report_id": report_id, "request_id": failure_details["request_id"],
                    "attempt": failure_details["attempt"], "input_revision": task.input_revision,
                    "continuation_request_id": continuation.request_id,
                    "continuation_state": continuation.state, "wake_state": "created"})
            if event is not None:
                event.message_id = message_id
                await db.execute(update(ProjectExecution).where(ProjectExecution.id == execution_id)
                    .values(state_revision=ProjectExecution.state_revision + 1, updated_at=datetime.now(UTC)))
                db.info.setdefault("project_execution_recovery_tasks", set()).add(root.id)
                return True
    except Exception as exc:
        # Only the wake was rolled back. Refresh any ORM rows its gate touched
        # before preserving the outer report and adding closed blocking state.
        await db.refresh(execution)
        await db.refresh(task)
        await db.refresh(report)
        await db.refresh(message)
        reason = exc.code if isinstance(exc, ExecutionConflict) else "EXECUTION_LEAD_WAKE_DEFERRED"
        if not isinstance(exc, ExecutionConflict):
            log.warning("project_execution.recovery_wake_deferred task=%s reason=%s",
                task_id, type(exc).__name__)
        blocked = await _event(db, execution, wake_key + ":blocked:" + reason,
            "task_recovery_lead_wake_blocked", task_id=task_id,
            details={"recovery_report_id": report_id, "request_id": failure_details["request_id"],
                "attempt": failure_details["attempt"], "input_revision": task.input_revision,
                "wake_state": "blocked", "reason_code": reason})
        if blocked is not None:
            blocked.message_id = message_id
            await db.execute(update(ProjectExecution).where(ProjectExecution.id == execution_id)
                .values(state_revision=ProjectExecution.state_revision + 1, updated_at=datetime.now(UTC)))
            db.info.setdefault("project_execution_recovery_tasks", set()).add(task_id)
            return True
    return False


async def report_terminal_recovery(db, *, request_id: str, attempt_number: int) -> list[str]:
    """Once-only ops notice/root continuation, isolated from producer failure.

    Notification failure must not roll back the native failure/retry history.
    The human notice persists even when the lead cannot run; a later scanner
    resumes that notice with a separately deduplicated, authorized wake.
    """
    from anygarden.project_executions.qa_repairs import repair_savepoint
    from anygarden.project_executions.service import (
        ExecutionConflict,
        reconcile_execution,
    )

    turn = await db.get(AgentTurn, request_id)
    if turn is None or not turn.execution_id or not turn.task_id:
        return []
    execution_id, task_id = turn.execution_id, turn.task_id
    try:
        async with repair_savepoint(db):
            # Serialize with input/cancel/deadline mutations before reading the
            # current task and exact failure. This never increments revision.
            locked = await db.scalar(update(ProjectExecution).where(
                ProjectExecution.id == execution_id,
                ProjectExecution.input_revision == turn.execution_input_revision,
                ProjectExecution.status.in_(ACTIVE_STATES),
            ).values(state_revision=ProjectExecution.state_revision).returning(ProjectExecution.id))
            if locked is None:
                return []
            task = await db.get(Task, task_id)
            if task is None:
                return []
            await db.refresh(task)
            details = await terminal_recovery_details(db, task=task,
                request_id=request_id, attempt_number=attempt_number)
            if details is None:
                return []
            before = await db.scalar(select(ProjectExecution.state_revision).where(
                ProjectExecution.id == execution_id,
            ))
            await reconcile_execution(db, task, failure_details=details)
            after = await db.scalar(select(ProjectExecution.state_revision).where(
                ProjectExecution.id == execution_id,
            ))
            if after != before:
                db.info.setdefault("project_execution_recovery_tasks", set()).add(task_id)
                return [execution_id]
    except Exception as exc:
        # No exception text, engine stderr, lease or native handle is logged.
        log.warning("project_execution.recovery_notice_deferred task=%s reason=%s",
            task_id, exc.code if isinstance(exc, ExecutionConflict) else type(exc).__name__)
    return []


async def reconcile_terminal_recoveries(db, *, limit: int = 25) -> list[str]:
    """Catch up current final failures only; never alter task/result history."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Recovery reconciliation limit must be between 1 and 100")
    attempt_prefix = ("turn:" + AgentTurn.request_id + ":attempt:"
        + AgentTurn.active_attempt.cast(String))
    report_key = attempt_prefix + ":recovery-report"
    wake_key = attempt_prefix + ":recovery-wake"
    root = aliased(Task)
    wake_available = exists(select(root.id)
        .join(Participant, Participant.id == root.assignee_participant_id)
        .join(Agent, Agent.id == Participant.agent_id).where(
            root.id == ProjectExecution.root_task_id,
            root.execution_id == ProjectExecution.id,
            root.input_revision == ProjectExecution.input_revision,
            root.room_id == ProjectExecution.operating_room_id, root.status == "in_progress",
            Participant.room_id == ProjectExecution.operating_room_id,
            Participant.agent_id == ProjectExecution.lead_agent_id,
            Participant.role.in_({"owner", "admin", "member"}), Agent.desired_state == "running",
            ~exists(select(ExecutionStop.id).where(
                ExecutionStop.execution_id == ProjectExecution.id,
                ExecutionStop.status.not_in(SAFE_STOP_STATES),
            ).correlate(ProjectExecution)),
        ))
    rows = list((await db.execute(select(AgentTurn.request_id, AgentTurn.active_attempt)
        .join(Task, Task.id == AgentTurn.task_id)
        .join(ProjectExecution, ProjectExecution.id == AgentTurn.execution_id)
        .where(
            ProjectExecution.status.in_(ACTIVE_STATES),
            or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > datetime.now(UTC)),
            Task.execution_id == ProjectExecution.id, Task.input_revision == ProjectExecution.input_revision,
            Task.id != ProjectExecution.root_task_id, Task.status.in_({"blocked", "failed"}),
            Task.source_message_id == AgentTurn.trigger_message_id,
            AgentTurn.execution_input_revision == ProjectExecution.input_revision,
            AgentTurn.state == "failed", AgentTurn.accepted_message_id.is_(None),
            exists(select(ProjectExecutionEvent.id).where(
                ProjectExecutionEvent.execution_id == ProjectExecution.id,
                ProjectExecutionEvent.task_id == Task.id,
                ProjectExecutionEvent.event_type == "task_recovery",
                ProjectExecutionEvent.event_key.in_([
                    attempt_prefix + ":recovery:exhausted",
                    attempt_prefix + ":recovery:action_required",
                ]),
            )),
            or_(
                ~exists(select(ProjectExecutionEvent.id).where(
                    ProjectExecutionEvent.execution_id == ProjectExecution.id,
                    ProjectExecutionEvent.task_id == Task.id,
                    ProjectExecutionEvent.event_key == report_key,
                )),
                wake_available & ~exists(select(ProjectExecutionEvent.id).where(
                    ProjectExecutionEvent.execution_id == ProjectExecution.id,
                    ProjectExecutionEvent.task_id == Task.id,
                    ProjectExecutionEvent.event_key == wake_key,
                )),
            ),
        ).order_by(AgentTurn.updated_at, AgentTurn.request_id).limit(limit))).all())
    changed = []
    for request_id, attempt_number in rows:
        changed.extend(await report_terminal_recovery(db, request_id=request_id,
            attempt_number=attempt_number))
    return list(dict.fromkeys(changed))


async def fanout_recovery_updates(session_factory, manager, task_ids) -> None:
    """Bounded postcommit invalidation; a failed push never retries native work."""
    if manager is None or not task_ids:
        return
    from anygarden.messages.service import fanout_task_event
    from anygarden.project_executions.serialization import fanout_execution_update

    executions = set()
    for task_id in set(task_ids):
        try:
            async with session_factory() as db:
                task = await db.get(Task, task_id)
                room = await db.get(Room, task.room_id) if task else None
                execution = await db.get(ProjectExecution, task.execution_id) if task and task.execution_id else None
                if task is None or room is None or execution is None or room.project_id != execution.project_id:
                    continue
                await asyncio.wait_for(fanout_task_event(db, manager=manager, event="updated",
                    task=task, room_name=room.name), timeout=2)
                if execution.id not in executions:
                    await fanout_execution_update(db, manager=manager, execution_id=execution.id)
                    executions.add(execution.id)
        except Exception:  # noqa: BLE001 — state is already committed; polling recovers
            log.warning("task_recovery_fanout_deferred", extra={"task_id": task_id})


def _retry_digest(body):
    from anygarden.project_executions.service import ExecutionConflict

    try:
        if set(body) != {"operation_id", "expected_input_revision", "expected_request_id", "expected_attempt"}:
            raise ValueError
        for key in ("operation_id", "expected_request_id"):
            if str(UUID(body[key])) != body[key]:
                raise ValueError
        for key in ("expected_input_revision", "expected_attempt"):
            if type(body[key]) is not int or body[key] < 1:
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ExecutionConflict("RETRY_INPUT_INVALID", "Provide a canonical operation ID and the exact failed task attempt") from None
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def retry_task(db, *, identity, task_id: str, body: dict):
    """Authorize one explicit bounded retry; keep task/request and old attempts."""
    from anygarden.project_executions.mutations import authorize_mutation
    from anygarden.project_executions.service import ExecutionConflict, _event

    with db.no_autoflush:
        binding = (await db.execute(select(Task.execution_id, Task.input_revision)
            .where(Task.id == task_id))).first()
    if binding is None or binding.execution_id is None:
        raise HTTPException(404, "Execution task not found")
    execution, access = await authorize_mutation(db, identity=identity, execution_id=binding.execution_id)
    task = await db.get(Task, task_id, populate_existing=True)
    if task is None or (task.execution_id, task.input_revision) != tuple(binding):
        raise ExecutionConflict("RETRY_EXECUTION_CHANGED", "Task execution binding changed; reload before retrying")
    room = await db.get(Room, task.room_id, populate_existing=True)
    if room is None or room.project_id != execution.project_id:
        raise HTTPException(404, "Execution task not found")
    digest = _retry_digest(body)
    key = f"task:{task.id}:retry-operation:{body['operation_id']}"

    async def existing_operation():
        existing = await db.scalar(select(ProjectExecutionEvent).where(
            ProjectExecutionEvent.execution_id == execution.id, ProjectExecutionEvent.task_id == task.id,
            ProjectExecutionEvent.event_key == key,
        ))
        if existing is not None:
            if existing.details.get("request_sha256") != digest:
                raise ExecutionConflict("RETRY_OPERATION_CONFLICT", "The retry operation ID was used with a different request")
            return {"operation_id": body["operation_id"], "request_id": existing.details["request_id"],
                "attempt": existing.details["attempt"], "duplicate": True}
        return None

    duplicate = await existing_operation()
    if duplicate:
        return task, duplicate
    locked = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == execution.id, ProjectExecution.input_revision == body["expected_input_revision"],
        ProjectExecution.status.in_(ACTIVE_STATES),
        exists(select(Room.id).where(Room.id == ProjectExecution.operating_room_id,
            Room.project_id == ProjectExecution.project_id, Room.archived_at.is_(None))),
        True if access.is_global_admin else exists(select(Participant.id).where(
            Participant.room_id == ProjectExecution.operating_room_id, Participant.user_id == identity.id,
            or_(Participant.role.in_({"admin", "owner"}),
                (Participant.role == "member") & (ProjectExecution.owner_user_id == identity.id)),
        )),
    ).values(state_revision=ProjectExecution.state_revision).returning(ProjectExecution.id))
    duplicate = await existing_operation()
    if duplicate:
        await db.refresh(task)
        return task, duplicate
    if locked is None:
        raise ExecutionConflict("RETRY_EXECUTION_CHANGED", "Execution input or permissions changed; reload before retrying")
    await db.refresh(execution)
    task = await db.get(Task, task_id, populate_existing=True, with_for_update=True)
    if task is None or (task.execution_id, task.input_revision) != tuple(binding):
        raise ExecutionConflict("RETRY_EXECUTION_CHANGED", "Task execution binding changed before retry")
    turn = await db.scalar(select(AgentTurn).where(
        AgentTurn.request_id == body["expected_request_id"],
        AgentTurn.execution_id == execution.id,
        AgentTurn.execution_input_revision == task.input_revision,
        AgentTurn.task_id == task.id,
    ).execution_options(populate_existing=True).with_for_update())
    attempt = await db.scalar(select(AgentTurnAttempt).where(
        AgentTurnAttempt.turn_id == body["expected_request_id"],
        AgentTurnAttempt.attempt_number == body["expected_attempt"],
    ).execution_options(populate_existing=True).with_for_update()) if turn is not None else None
    denial = await _retry_denial(db, task=task, execution=execution, turn=turn, attempt=attempt)
    if denial:
        if denial in {"EXECUTION_NATIVE_INVOCATION_LIMIT", "EXECUTION_USAGE_LIMIT_REACHED"}:
            db.info.setdefault("project_execution_usage_denials", {})[execution.id] = denial
        raise ExecutionConflict(f"TASK_RETRY_{denial}", "This task cannot safely retry; reload its recovery state and resolve the indicated blocker")
    # Do not reopen an old request once a newer continuation exists for this task.
    latest = await db.scalar(select(AgentTurn.request_id).where(
        AgentTurn.task_id == task.id, AgentTurn.execution_id == execution.id,
        AgentTurn.execution_input_revision == task.input_revision,
    ).order_by(AgentTurn.created_at.desc(), AgentTurn.request_id.desc()).limit(1))
    if latest != turn.request_id:
        raise ExecutionConflict("TASK_RETRY_REQUEST_SUPERSEDED", "Retry the task's current failed request")
    agent = await db.get(Agent, turn.agent_id, populate_existing=True)
    generation = await db.scalar(update(Agent).where(
        Agent.id == turn.agent_id, Agent.generation == agent.generation,
        Agent.desired_state == "running", Agent.actual_state == "running", Agent.pending_generation.is_(None),
    ).values(generation=Agent.generation).returning(Agent.generation))
    if generation is None:
        raise ExecutionConflict("TASK_RETRY_AGENT_RESTARTING", "Wait for the restored agent configuration to finish starting before retrying")
    number = attempt.attempt_number + 1
    now = datetime.now(UTC)
    changed = await db.scalar(update(AgentTurn).where(
        AgentTurn.request_id == turn.request_id, AgentTurn.task_id == task.id,
        AgentTurn.execution_id == execution.id, AgentTurn.execution_input_revision == task.input_revision,
        AgentTurn.active_attempt == body["expected_attempt"], AgentTurn.state.in_({"failed", "cancelled"}),
        AgentTurn.retry_count < AgentTurn.max_retries,
    ).values(state="retrying", active_attempt=number, retry_count=AgentTurn.retry_count + 1,
        terminal_reason=None, completed_at=None, updated_at=now).returning(AgentTurn.request_id))
    if changed is None:
        raise ExecutionConflict("TASK_RETRY_ATTEMPT_CHANGED", "The current attempt changed before retry")
    # Fenced delivery rows cannot feed a concurrent old invocation after retry.
    await db.execute(update(AgentTurnOutbox).where(AgentTurnOutbox.turn_id == turn.request_id,
        AgentTurnOutbox.state.in_({"pending", "delivering"})).values(state="cancelled"))
    fresh = AgentTurnAttempt(id=str(uuid4()), turn_id=turn.request_id, agent_id=turn.agent_id,
        attempt_number=number, generation=int(generation), lease_token=secrets.token_urlsafe(32), state="pending")
    db.add(fresh)
    db.add(AgentTurnOutbox(id=str(uuid4()), turn_id=turn.request_id, attempt_id=fresh.id,
        room_id=turn.room_id, participant_id=turn.target_participant_id, state="pending", available_at=now))
    await db.flush()
    await db.refresh(turn)
    if not await on_turn_recovery(db, turn=turn, attempt=fresh, phase="retrying", reason_code=None):
        raise ExecutionConflict("TASK_RETRY_STATUS_CHANGED", "Task acquired an accepted result or changed before retry")
    await _event(db, execution, key, "task_retry_requested", task_id=task.id,
        details={"request_sha256": digest, "actor_user_id": identity.id, "request_id": turn.request_id,
            "previous_attempt": attempt.attempt_number, "attempt": number, "input_revision": task.input_revision})
    await db.refresh(task)
    return task, {"operation_id": body["operation_id"], "request_id": turn.request_id,
        "attempt": number, "duplicate": False}
