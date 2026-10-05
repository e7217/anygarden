"""Exact native permit accounting; never a provider-request or cost hard cap.

All helpers participate in the caller transaction and commit nothing. Start
callers MUST enclose reservation, Attempt.local_execution_id CAS, and their
permit audit in one savepoint; denial/exception rolls back the whole savepoint.
The caller retains current lease/workspace/participant/start authorization.
Execution-row write locks serialize reservations, adoption, and settlement;
related-row queries are issued AFTER that lock, not from a stale COUNT snapshot.

Accounting-only terminal ingress may use a historical attempt/generation after
revision changes, cancellation, or completion. It still requires the original
stored lease and exact permitted UUID. It grants no workflow/result/start right.
Limits count native start permissions, including safe no-start receipts. A CLI
invocation's internal provider requests are unmeasured. Token limits are only
observed terminal thresholds; pending/unknown usage and unavailable costs stay
explicit. Intent admission is for NEW work, never an already permitted call's
publication/finalization. Permit N may finish; the next permit is denied.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    ActivityLog,
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    Task,
)
from anygarden.db.native_invocation_models import NativeInvocationAccounting
from anygarden.project_executions.authorization import ACTIVE_STATES
from anygarden.rooms.authorization import AGENT_EXECUTION_ROLES

VERSION = "native-invocation-v1"
SOURCE = "codex-session-cumulative-v1"
MAX_COUNTER = (1 << 63) - 1
TOKEN_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens")
MEASURED = frozenset({"fresh_measured", "measured_delta"})
USAGE_STATUSES = MEASURED | frozenset({
    "baseline_missing", "baseline_invalidated", "counter_rollback", "session_changed",
    "session_unobserved", "terminal_usage_missing", "provider_usage_invalid",
    "process_outcome_unknown", "not_started",
})
BASELINE_STATUSES = frozenset({"fresh", "valid", "absent", "invalidated"})
METADATA_KEYS = frozenset({"source", "status", "baseline_status", "provider_cumulative",
    "baseline", "requested_session_sha256", "observed_session_sha256",
    "baseline_execution_sha256"})
NATIVE_REASONS = frozenset({"MODEL_CONFIGURATION_INVALID", "AUTHENTICATION_FAILED",
    "MODEL_TRANSIENT_FAILURE", "MODEL_EXECUTION_FAILED", "PROCESS_OUTCOME_UNKNOWN"})
HEX_SHA = re.compile(r"^[0-9a-f]{64}$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,159}$")
NATIVE_LIMIT = "EXECUTION_NATIVE_INVOCATION_LIMIT"
USAGE_LIMIT = "EXECUTION_USAGE_LIMIT_REACHED"


@dataclass(frozen=True)
class NativeAdmission:
    allowed: bool
    duplicate: bool = False
    reason_code: str | None = None
    row_id: str | None = None


@dataclass(frozen=True)
class NativeSettlement:
    accepted: bool
    duplicate: bool = False
    execution_id: str | None = None
    summary_changed: bool = False
    limit_reason: str | None = None


@dataclass(frozen=True)
class AdmissionDisposition:
    allowed: bool
    wait: bool = False
    reason_code: str | None = None


class _AccountingScopeChanged(Exception):
    """Roll back newly acquired row locks after one-way source adoption."""


def _now():
    return datetime.now(UTC)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":")).encode()).hexdigest()


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _count(value):
    return value if type(value) is int and 0 <= value <= MAX_COUNTER else None


def _cost(value):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal("99999999999.999999999"):
            return None
        rounded = number.quantize(Decimal("0.000000001"))
        # Do not round tiny positive costs into a fabricated zero.
        return rounded if rounded == number else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _counter_dict(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - set(TOKEN_KEYS):
        raise ValueError("INVALID_COUNTER_PROVENANCE")
    result = {}
    for key in TOKEN_KEYS:
        number = value.get(key)
        if number is not None and _count(number) is None:
            raise ValueError("INVALID_COUNTER_PROVENANCE")
        result[key] = number
    return result


def _metadata(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) - METADATA_KEYS
        or value.get("source") != SOURCE or value.get("status") not in USAGE_STATUSES
        or value.get("baseline_status") not in BASELINE_STATUSES):
        raise ValueError("INVALID_USAGE_PROVENANCE")
    result = {"source": SOURCE, "status": value["status"],
              "baseline_status": value["baseline_status"]}
    for key in ("provider_cumulative", "baseline"):
        result[key] = _counter_dict(value.get(key))
    for key in ("requested_session_sha256", "observed_session_sha256", "baseline_execution_sha256"):
        hashed = value.get(key)
        if hashed is not None and (not isinstance(hashed, str) or not HEX_SHA.fullmatch(hashed)):
            raise ValueError("INVALID_USAGE_PROVENANCE")
        result[key] = hashed
    return result


def _normalized_settlement(row, frame):
    """Only whitelisted provenance/numbers enter storage or its immutable hash."""
    process = getattr(frame, "native_process_state", None)
    outcome = getattr(frame, "native_outcome", None)
    native_reason = getattr(frame, "native_reason_code", None)
    terminal = {
        "local_execution_id": row.local_execution_id,
        "process_state": process if process in {"not_started", "running", "finished", "stopped", "unknown"} else "unknown",
        "outcome": outcome if outcome in {"succeeded", "failed", "cancelled", "unknown"} else "unknown",
        "reason_code": native_reason if native_reason in NATIVE_REASONS else None,
        "transient": outcome == "failed" and native_reason == "MODEL_TRANSIENT_FAILURE"
            and getattr(frame, "native_transient", None) is True,
    }
    top = {key: _count(getattr(frame, key, None)) for key in ("input_tokens", "output_tokens")}
    top_invalid = any(getattr(frame, key, None) is not None and top[key] is None for key in top)
    try:
        metadata = _metadata(getattr(frame, "usage_metadata", None))
        reason = metadata["status"] if metadata else "terminal_usage_missing"
    except (ValueError, TypeError):
        metadata, reason = None, "provider_usage_invalid"
    tokens = dict.fromkeys(TOKEN_KEYS)
    status = "unknown"
    safe_pair = ((outcome == "succeeded" and process == "finished")
        or (outcome == "failed" and process in {"finished", "stopped", "not_started"})
        or (outcome == "cancelled" and process in {"stopped", "not_started"}))
    if process == "not_started":
        if (outcome in {"failed", "cancelled", "unknown"} and not top_invalid
            and all(v in {None, 0} for v in top.values())
            and (metadata is None or metadata["status"] == "not_started")):
            status, reason = "not_started", "not_started"
        else:
            reason = "provider_usage_invalid"
    elif not safe_pair or process in {"unknown", "running"}:
        reason = "process_outcome_unknown"
    elif metadata and metadata["status"] in MEASURED and not top_invalid:
        raw, baseline = metadata["provider_cumulative"], metadata["baseline"]
        fresh = metadata["status"] == "fresh_measured"
        valid = (raw is not None and baseline is not None
            and all(raw[key] is not None and baseline[key] is not None for key in ("input_tokens", "output_tokens"))
            and metadata["observed_session_sha256"] is not None
            and ((fresh and metadata["baseline_status"] == "fresh"
                  and metadata["requested_session_sha256"] is None
                  and metadata["baseline_execution_sha256"] is None
                  and all(baseline[key] == 0 for key in TOKEN_KEYS))
                 or (not fresh and metadata["baseline_status"] == "valid"
                     and metadata["requested_session_sha256"] == metadata["observed_session_sha256"]
                     and metadata["requested_session_sha256"] is not None
                     and metadata["baseline_execution_sha256"] is not None)))
        if valid:
            for key in TOKEN_KEYS:
                if raw[key] is not None and baseline[key] is not None:
                    tokens[key] = raw[key] - baseline[key]
                    valid = valid and _count(tokens[key]) is not None
            valid = valid and all(top[key] == tokens[key] for key in top)
            for counters in (raw, baseline, tokens):
                valid = valid and (counters["cached_input_tokens"] is None
                    or counters["input_tokens"] is not None
                    and counters["cached_input_tokens"] <= counters["input_tokens"])
        if valid:
            status = "measured"
        else:
            tokens, reason = dict.fromkeys(TOKEN_KEYS), "provider_usage_invalid"
    elif top_invalid:
        reason = "provider_usage_invalid"
    if status != "measured":
        tokens = dict.fromkeys(TOKEN_KEYS)
    model = getattr(frame, "model", None)
    model = model if isinstance(model, str) and MODEL_NAME.fullmatch(model) else None
    reported_cost = _cost(getattr(frame, "cost_usd", None))
    cost = reported_cost if status == "measured" else None
    normalized = {"usage_status": status, "closure_reason": reason,
        "closure_source": SOURCE if metadata else "native-terminal-v1",
        **tokens, "cost_usd": cost, "model": model,
        "duration_ms": _count(getattr(frame, "duration_ms", None)),
        "usage_metadata": metadata, "native_terminal": terminal}
    # Adoptable task/execution/revision and arrival time are intentionally NOT
    # hashed: the same original unbound receipt must replay after adoption.
    hashed = {**normalized, "cost_usd": str(cost) if cost is not None else None,
        "reported_top_tokens": top,
        "reported_cost_usd": str(reported_cost) if reported_cost is not None else None,
        "identity": {"attempt_id": row.attempt_id, "local_execution_id": row.local_execution_id,
            "request_id": row.request_id, "agent_id": row.agent_id, "room_id": row.room_id,
            "attempt_number": row.attempt_number, "generation": row.generation}}
    normalized["settlement_sha256"] = _digest(hashed)
    return normalized


async def _lock_execution(db, execution_id):
    # Explicitly keep updated_at unchanged: locks/identical replays are not new
    # accounting observations and must not change the public state revision.
    found = await db.scalar(update(ProjectExecution).where(ProjectExecution.id == execution_id)
        .values(state_revision=ProjectExecution.state_revision,
                updated_at=ProjectExecution.updated_at).returning(ProjectExecution.id))
    return await db.get(ProjectExecution, found, populate_existing=True) if found else None


def _identity_matches(row, turn, attempt, local_id):
    return (row.attempt_id == attempt.id and row.local_execution_id == local_id
        and row.request_id == turn.request_id and row.agent_id == turn.agent_id
        and row.room_id == turn.room_id and row.attempt_number == attempt.attempt_number
        and row.generation == attempt.generation)


async def _conflict(db, row, *, reason, settlement_sha=None):
    details = {"accounting_id": row.id, "reason_code": reason,
               "settlement_sha256": settlement_sha}
    if row.execution_id:
        key = f"native-accounting:{row.id}:conflict:{reason}:{settlement_sha or 'identity'}"
        existing = await db.scalar(select(ProjectExecutionEvent.id).where(ProjectExecutionEvent.event_key == key))
        if existing is None:
            db.add(ProjectExecutionEvent(execution_id=row.execution_id, task_id=row.task_id,
                event_type="native_usage_accounting_conflict", event_key=key, details=details))
    else:
        existing = await db.scalar(select(ActivityLog.id).where(
            ActivityLog.request_id == row.request_id, ActivityLog.agent_id == row.agent_id,
            ActivityLog.room_id == row.room_id, ActivityLog.event_type == "native_usage_accounting_conflict",
            ActivityLog.details["accounting_id"].as_string() == row.id,
            ActivityLog.details["reason_code"].as_string() == reason,
            (ActivityLog.details["settlement_sha256"].as_string() == settlement_sha
             if settlement_sha is not None else ActivityLog.details["settlement_sha256"].as_string().is_(None)),
        ).limit(1))
        if existing is None:
            db.add(ActivityLog(agent_id=row.agent_id, room_id=row.room_id,
                request_id=row.request_id, event_type="native_usage_accounting_conflict", details=details))
    await db.flush()


def _limit(limits, key):
    value = (limits or {}).get(key)
    if value is not None and (type(value) is not int or not 1 <= value <= MAX_COUNTER):
        raise ValueError("INVALID_EXECUTION_LIMIT")
    return value


def _admission(summary, limits):
    native_cap = _limit(limits, "max_native_invocations")
    token_cap = _limit(limits, "max_total_tokens")
    if native_cap is not None:
        if not summary["coverage"]["native_reservation_complete"]:
            return AdmissionDisposition(False, reason_code="EXECUTION_USAGE_ACCOUNTING_INCOMPLETE")
        if summary["native_invocations_reserved"] >= native_cap:
            return AdmissionDisposition(False, reason_code=NATIVE_LIMIT)
    if (token_cap is not None and summary["known_total_tokens"] is not None
        and summary["known_total_tokens"] >= token_cap):
        return AdmissionDisposition(False, reason_code=USAGE_LIMIT)
    # This is an OBSERVED threshold, not a hard token reservation. Waiting for
    # every pending parent would deadlock lead→child delegation. Missingness is
    # public in the summary; it is never reclassified as known zero.
    return AdmissionDisposition(True)


async def admission_disposition(db: AsyncSession, *, execution: ProjectExecution,
                                phase: Literal["intent", "native"]) -> AdmissionDisposition:
    """Check NEW intent/native admission; current permitted tools bypass this."""
    if phase not in {"intent", "native"}:
        raise ValueError("INVALID_ADMISSION_PHASE")
    current = await _lock_execution(db, execution.id)
    if current is None or current.status not in ACTIVE_STATES:
        return AdmissionDisposition(False, reason_code="EXECUTION_NOT_ACTIVE")
    return _admission(await execution_usage_summary(db, execution_id=current.id), current.limits)


async def _write_summary(db, execution_id):
    summary = await execution_usage_summary(db, execution_id=execution_id)
    await db.execute(update(ProjectExecution).where(ProjectExecution.id == execution_id)
        .values(usage_summary=summary, state_revision=ProjectExecution.state_revision + 1,
                updated_at=_now()))
    await db.flush()
    return summary


async def is_permitted_native_invocation(db: AsyncSession, *, turn: AgentTurn,
                                        attempt: AgentTurnAttempt) -> bool:
    """Exact existing permit for transport replay, never new start authority.

    Callers must still validate their lease/current delivery identity. This
    exception only bypasses a NEW-admission check for the identical previously
    reserved attempt. A fresh retry attempt/UUID cannot inherit the exception.
    """
    if (attempt.turn_id != turn.request_id or attempt.agent_id != turn.agent_id
        or not _uuid(attempt.local_execution_id)):
        return False
    row = await db.scalar(select(NativeInvocationAccounting).where(
        NativeInvocationAccounting.attempt_id == attempt.id))
    return (row is not None and _identity_matches(row, turn, attempt, attempt.local_execution_id)
        and row.task_id == turn.task_id and row.execution_id == turn.execution_id
        and row.input_revision == turn.execution_input_revision)


async def reserve_native_invocation(db: AsyncSession, *, turn: AgentTurn,
        attempt: AgentTurnAttempt, local_execution_id: str) -> NativeAdmission:
    """Reserve once, AFTER authoritative start authorization, before its commit.

    Denial does not consume a slot. A uniqueness/transaction exception must be
    rolled back by the caller's enclosing permit savepoint, never committed.
    """
    if not _uuid(local_execution_id):
        return NativeAdmission(False, reason_code="LOCAL_EXECUTION_ID_INVALID")
    binding = (await db.execute(select(
        AgentTurn.execution_id, AgentTurn.execution_input_revision,
    ).where(AgentTurn.request_id == turn.request_id))).first()
    if binding is None:
        return NativeAdmission(False, reason_code="NATIVE_ACCOUNTING_BINDING_CONFLICT")
    # The bound execution is always locked before its Turn/Attempt. A caller
    # must roll back its permit savepoint if the one-way binding changed while
    # this unlocked snapshot was being acquired.
    execution = await _lock_execution(db, binding.execution_id) if binding.execution_id else None
    current_turn = await db.get(AgentTurn, turn.request_id, populate_existing=True, with_for_update=True)
    if (current_turn is None
        or (current_turn.execution_id, current_turn.execution_input_revision) != tuple(binding)):
        return NativeAdmission(False, reason_code="NATIVE_ACCOUNTING_BINDING_CONFLICT")
    current_attempt = await db.get(AgentTurnAttempt, attempt.id, populate_existing=True, with_for_update=True)
    if (current_turn is None or current_attempt is None
        or current_attempt.turn_id != current_turn.request_id
        or current_attempt.agent_id != current_turn.agent_id
        or current_attempt.attempt_number != current_turn.active_attempt
        or current_attempt.state not in {"leased", "started"}
        or current_turn.state not in {"pending", "leased", "retrying"}
        or current_attempt.local_execution_id not in {None, local_execution_id}):
        return NativeAdmission(False, reason_code="NATIVE_ACCOUNTING_BINDING_CONFLICT")
    agent = await db.get(Agent, current_turn.agent_id, populate_existing=True)
    participant = await db.get(Participant, current_turn.target_participant_id, populate_existing=True)
    room = await db.get(Room, current_turn.room_id, populate_existing=True)
    if (agent is None or agent.generation != current_attempt.generation or agent.desired_state != "running"
        or participant is None or participant.agent_id != agent.id or participant.room_id != current_turn.room_id
        or participant.role not in AGENT_EXECUTION_ROLES or room is None or room.archived_at is not None
        or current_attempt.lease_expires_at is None or current_attempt.lease_expires_at <= _now()):
        return NativeAdmission(False, reason_code="STALE_TURN_PROOF")
    if current_turn.execution_id is not None:
        task = await db.get(Task, current_turn.task_id, populate_existing=True) if current_turn.task_id else None
        if (execution is None or task is None or room.project_id != execution.project_id
            or execution.status not in ACTIVE_STATES or execution.input_revision != current_turn.execution_input_revision
            or task.execution_id != execution.id or task.input_revision != current_turn.execution_input_revision
            or task.room_id != current_turn.room_id or task.assignee_participant_id != current_turn.target_participant_id):
            return NativeAdmission(False, reason_code="TURN_EXECUTION_BINDING_INVALID")
    elif current_turn.execution_input_revision is not None:
        return NativeAdmission(False, reason_code="TURN_EXECUTION_BINDING_INVALID")
    existing = await db.scalar(select(NativeInvocationAccounting).where(or_(
        NativeInvocationAccounting.attempt_id == current_attempt.id,
        NativeInvocationAccounting.local_execution_id == local_execution_id)).with_for_update())
    if existing is not None:
        same = (_identity_matches(existing, current_turn, current_attempt, local_execution_id)
            and existing.task_id == current_turn.task_id
            and existing.execution_id == current_turn.execution_id
            and existing.input_revision == current_turn.execution_input_revision)
        return NativeAdmission(same, same, None if same else "NATIVE_ACCOUNTING_BINDING_CONFLICT", existing.id)
    if execution is not None:
        disposition = _admission(await execution_usage_summary(db, execution_id=execution.id), execution.limits)
        if not disposition.allowed:
            return NativeAdmission(False, reason_code=disposition.reason_code)
        predicates = [ProjectExecution.id == execution.id, ProjectExecution.status.in_(ACTIVE_STATES),
            ProjectExecution.input_revision == current_turn.execution_input_revision,
            ProjectExecution.native_invocations_reserved == execution.native_invocations_reserved,
            ProjectExecution.native_invocations_reserved < MAX_COUNTER]
        cap = _limit(execution.limits, "max_native_invocations")
        if cap is not None:
            predicates.append(ProjectExecution.native_invocations_reserved < cap)
        changed = await db.scalar(update(ProjectExecution).where(*predicates)
            .values(native_invocations_reserved=ProjectExecution.native_invocations_reserved + 1,
                    updated_at=ProjectExecution.updated_at).returning(ProjectExecution.id))
        if changed is None:
            return NativeAdmission(False, reason_code=NATIVE_LIMIT)
    stamp = _now()
    row = NativeInvocationAccounting(attempt_id=current_attempt.id, local_execution_id=local_execution_id,
        request_id=current_turn.request_id, agent_id=current_turn.agent_id, room_id=current_turn.room_id,
        task_id=current_turn.task_id, execution_id=current_turn.execution_id,
        input_revision=current_turn.execution_input_revision, attempt_number=current_attempt.attempt_number,
        generation=current_attempt.generation, reserved_at=stamp, as_of=stamp, usage_status="pending")
    db.add(row)
    await db.flush()
    if execution is not None:
        await _write_summary(db, execution.id)
    return NativeAdmission(True, row_id=row.id)


async def adopt_source_invocations(db: AsyncSession, *, execution: ProjectExecution,
                                  turn: AgentTurn) -> dict:
    """Only original root source adopts NULL bindings; child/continuation is no-op."""
    if (turn.task_id != execution.root_task_id or turn.agent_id != execution.lead_agent_id
        or turn.room_id != execution.operating_room_id or turn.trigger_message_id != execution.source_message_id):
        return await execution_usage_summary(db, execution_id=execution.id)
    current = await _lock_execution(db, execution.id)
    current_turn = await db.get(AgentTurn, turn.request_id, populate_existing=True, with_for_update=True)
    if (current is None or current_turn is None or current_turn.task_id != current.root_task_id
        or current_turn.agent_id != current.lead_agent_id or current_turn.room_id != current.operating_room_id
        or current_turn.trigger_message_id != current.source_message_id
        or current_turn.execution_id != current.id or current_turn.execution_input_revision != 1):
        raise ValueError("EXECUTION_USAGE_SOURCE_BINDING_INVALID")
    attempts = {row.id: row for row in await db.scalars(select(AgentTurnAttempt).where(
        AgentTurnAttempt.turn_id == current_turn.request_id).with_for_update())}
    accounting = list(await db.scalars(select(NativeInvocationAccounting).where(
        NativeInvocationAccounting.request_id == current_turn.request_id).with_for_update()))
    unbound = []
    for row in accounting:
        source_attempt = attempts.get(row.attempt_id)
        if (source_attempt is None or not _identity_matches(row, current_turn, source_attempt, source_attempt.local_execution_id)
            or row.task_id not in {None, current.root_task_id}
            or (row.execution_id, row.input_revision) not in {(None, None), (current.id, 1)}):
            raise ValueError("EXECUTION_USAGE_SOURCE_BINDING_CONFLICT")
        if row.execution_id is None:
            unbound.append(row)
    if unbound:
        changed = await db.scalar(update(ProjectExecution).where(ProjectExecution.id == current.id,
            ProjectExecution.native_invocations_reserved == current.native_invocations_reserved,
            ProjectExecution.native_invocations_reserved <= MAX_COUNTER - len(unbound))
            .values(native_invocations_reserved=ProjectExecution.native_invocations_reserved + len(unbound),
                    updated_at=ProjectExecution.updated_at).returning(ProjectExecution.id))
        if changed is None:
            raise ValueError("EXECUTION_USAGE_SOURCE_COUNTER_CONFLICT")
        for row in unbound:
            changed = await db.scalar(update(NativeInvocationAccounting).where(
                NativeInvocationAccounting.id == row.id, NativeInvocationAccounting.execution_id.is_(None),
                NativeInvocationAccounting.input_revision.is_(None),
                NativeInvocationAccounting.task_id == row.task_id)
                .values(execution_id=current.id, input_revision=1, task_id=current.root_task_id)
                .returning(NativeInvocationAccounting.id))
            if changed is None:
                raise ValueError("EXECUTION_USAGE_SOURCE_BINDING_CONFLICT")
    key = f"execution:{current.id}:usage-source:{current_turn.request_id}"
    source = await db.scalar(select(ProjectExecutionEvent).where(ProjectExecutionEvent.event_key == key))
    created = source is None
    if created:
        source = ProjectExecutionEvent(execution_id=current.id, task_id=current.root_task_id,
            event_key=key, event_type="native_source_invocations_adopted", details={
                "version": VERSION, "request_id": current_turn.request_id, "input_revision": 1,
                "accounted_source_attempts": len(accounting),
                "initial_limit_declared_after_native_start": True})
        db.add(source)
    elif (source.execution_id != current.id or source.task_id != current.root_task_id
          or (source.details or {}).get("request_id") != current_turn.request_id):
        raise ValueError("EXECUTION_USAGE_SOURCE_BINDING_CONFLICT")
    await db.flush()
    if unbound or created:
        return await _write_summary(db, current.id)
    return await execution_usage_summary(db, execution_id=current.id)


async def settle_native_usage(db: AsyncSession, *, agent_id: str, room_id: str,
                             frame) -> NativeSettlement:
    """Immutable terminal settlement; historical proof grants accounting ONLY."""
    if getattr(frame, "event", None) != "engine_call_finished" or getattr(frame, "room_id", None) != room_id:
        return NativeSettlement(False)
    number, generation = getattr(frame, "turn_attempt", None), getattr(frame, "turn_generation", None)
    local_id, lease = getattr(frame, "local_execution_id", None), getattr(frame, "turn_lease", None)
    if (type(number) is not int or number < 1 or type(generation) is not int or generation < 0
        or not _uuid(local_id) or not isinstance(lease, str)):
        return NativeSettlement(False)
    binding = (await db.execute(select(
        AgentTurn.execution_id, AgentTurn.execution_input_revision,
    ).where(AgentTurn.request_id == getattr(frame, "request_id", None)))).first()
    if binding is None:
        return NativeSettlement(False)
    try:
        async with db.begin_nested():
            if binding.execution_id and await _lock_execution(db, binding.execution_id) is None:
                return NativeSettlement(False)
            turn = await db.get(AgentTurn, getattr(frame, "request_id", None),
                                populate_existing=True, with_for_update=True)
            if (turn is None
                or (turn.execution_id, turn.execution_input_revision) != tuple(binding)):
                # The original room turn may have been adopted after the
                # unlocked read. Release its Turn lock before the caller's
                # workflow gate can acquire the newly bound Execution lock.
                raise _AccountingScopeChanged
            if turn.agent_id != agent_id or turn.room_id != room_id:
                return NativeSettlement(False)
            attempt = await db.scalar(select(AgentTurnAttempt).where(
                AgentTurnAttempt.turn_id == turn.request_id,
                AgentTurnAttempt.attempt_number == number,
            ).execution_options(populate_existing=True).with_for_update())
            if (attempt is None or attempt.agent_id != agent_id or attempt.generation != generation
                or attempt.local_execution_id != local_id
                or not secrets.compare_digest(attempt.lease_token, lease)):
                return NativeSettlement(False)
            row = await db.scalar(select(NativeInvocationAccounting).where(
                NativeInvocationAccounting.attempt_id == attempt.id,
            ).execution_options(populate_existing=True).with_for_update())
    except _AccountingScopeChanged:
        return NativeSettlement(False)
    if row is None or not _identity_matches(row, turn, attempt, local_id):
        return NativeSettlement(False)
    if (not _identity_matches(row, turn, attempt, local_id)
        or row.task_id != turn.task_id or row.execution_id != turn.execution_id
        or row.input_revision != turn.execution_input_revision):
        await _conflict(db, row, reason="NATIVE_ACCOUNTING_BINDING_CONFLICT")
        return NativeSettlement(False, execution_id=row.execution_id)
    normalized = _normalized_settlement(row, frame)
    if row.usage_status != "pending":
        if row.settlement_sha256 == normalized["settlement_sha256"]:
            return NativeSettlement(True, True, row.execution_id)
        await _conflict(db, row, reason="NATIVE_USAGE_SETTLEMENT_CONFLICT",
                        settlement_sha=normalized["settlement_sha256"])
        return NativeSettlement(False, execution_id=row.execution_id)
    stamp = _now()
    changed = await db.scalar(update(NativeInvocationAccounting).where(
        NativeInvocationAccounting.id == row.id, NativeInvocationAccounting.usage_status == "pending",
        NativeInvocationAccounting.settled_at.is_(None), NativeInvocationAccounting.settlement_sha256.is_(None))
        .values(**normalized, settled_at=stamp, as_of=stamp).returning(NativeInvocationAccounting.id))
    if changed is None:
        raise ValueError("NATIVE_USAGE_SETTLEMENT_CONFLICT")
    await db.flush()
    if row.execution_id is None:
        return NativeSettlement(True)
    summary = await _write_summary(db, row.execution_id)
    execution = await db.get(ProjectExecution, row.execution_id, populate_existing=True)
    cap = _limit(execution.limits, "max_total_tokens")
    reason = USAGE_LIMIT if (cap is not None and summary["known_total_tokens"] is not None
        and summary["known_total_tokens"] >= cap) else None
    native_cap = _limit(execution.limits, "max_native_invocations")
    if reason is None and native_cap is not None and execution.native_invocations_reserved >= native_cap:
        # The last permitted call can end without another intent (a domain
        # blocker, for example). Its real terminal receipt must also close
        # the exhausted run, without waiting for a new denial or the deadline.
        # Other permitted calls keep their publication/finalization rights
        # until terminal, through close_execution_limit's existing deferral.
        reason = NATIVE_LIMIT
    return NativeSettlement(True, execution_id=row.execution_id, summary_changed=True, limit_reason=reason)


async def execution_usage_summary(db: AsyncSession, *, execution_id: str) -> dict:
    """All revisions and outcomes, with missing historical permit coverage exposed."""
    execution = await db.get(ProjectExecution, execution_id, populate_existing=True)
    if execution is None:
        raise ValueError("EXECUTION_NOT_FOUND")
    accounting = list(await db.scalars(select(NativeInvocationAccounting).where(
        NativeInvocationAccounting.execution_id == execution_id).order_by(NativeInvocationAccounting.id)))
    expected = list(await db.execute(select(AgentTurnAttempt.id, AgentTurnAttempt.local_execution_id)
        .join(AgentTurn, AgentTurn.request_id == AgentTurnAttempt.turn_id).where(
            AgentTurnAttempt.local_execution_id.is_not(None), or_(AgentTurn.execution_id == execution_id,
                AgentTurn.task_id.in_(select(Task.id).where(Task.execution_id == execution_id))))))
    matched = {(row.attempt_id, row.local_execution_id) for row in accounting}
    missing = sum((attempt_id, local_id) not in matched for attempt_id, local_id in expected)
    source = await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution_id,
        ProjectExecutionEvent.event_type == "native_source_invocations_adopted")
        .order_by(ProjectExecutionEvent.created_at, ProjectExecutionEvent.id).limit(1))
    source_recorded = source is not None
    counter_matches = execution.native_invocations_reserved == len(accounting)
    coverage_complete = source_recorded and bool(accounting) and missing == 0 and counter_matches

    def aggregate(items, complete):
        counts = Counter(row.usage_status for row in items)
        measured = [row for row in items if row.usage_status == "measured"]
        known = {}
        all_not_started = bool(items) and counts["not_started"] == len(items)
        for key in TOKEN_KEYS:
            values = [getattr(row, key) for row in measured if getattr(row, key) is not None]
            known[key] = sum(values) if values else 0 if all_not_started else None
        known_total = (known["input_tokens"] + known["output_tokens"]
            if known["input_tokens"] is not None and known["output_tokens"] is not None else None)
        token_complete = complete and not counts["pending"] and not counts["unknown"]
        cost_known = [row for row in items if row.cost_usd is not None]
        cost_complete = token_complete and bool(cost_known) and len(cost_known) == len(items)
        costs = sum((row.cost_usd for row in cost_known), Decimal(0)) if cost_known else None
        return {"invocation_count": len(items), "pending_invocations": counts["pending"],
            "measured_invocations": counts["measured"], "unknown_invocations": counts["unknown"],
            "not_started_invocations": counts["not_started"],
            "known_input_tokens": known["input_tokens"], "known_output_tokens": known["output_tokens"],
            "known_cached_input_tokens": known["cached_input_tokens"],
            "known_total_tokens": known_total,
            "input_tokens": known["input_tokens"] if token_complete else None,
            "output_tokens": known["output_tokens"] if token_complete else None,
            "cached_input_tokens": known["cached_input_tokens"] if token_complete
                and all(row.cached_input_tokens is not None for row in measured) else None,
            "total_tokens": known_total if token_complete else None,
            "known_cost_usd": str(costs) if costs is not None else None,
            "cost_usd": str(costs) if cost_complete else None,
            "cost_measured_invocations": len(cost_known),
            "cost_unknown_invocations": len(items) - len(cost_known),
            "unknown_reasons": dict(sorted(Counter(row.closure_reason or "terminal_usage_missing"
                for row in items if row.usage_status == "unknown").items()))}

    revisions = sorted({row.input_revision for row in accounting})
    timestamps = [row.as_of for row in accounting]
    if source is not None:
        timestamps.append(source.created_at)
    as_of = max(timestamps) if timestamps else execution.created_at
    summary = {"version": VERSION, **aggregate(accounting, coverage_complete),
        "native_invocations_reserved": execution.native_invocations_reserved,
        "provider_model_calls": None,
        "coverage": {"source_adoption_recorded": source_recorded,
            "missing_permitted_attempts": missing,
            # A caller may have reserved immediately before its Attempt UUID
            # CAS in the same required savepoint. Include that prospective
            # permit without publishing a stale lower expected count.
            "expected_permitted_attempts": len(set(expected) | matched),
            "accounting_counter_matches": counter_matches,
            "native_reservation_complete": coverage_complete,
            "status": "complete" if coverage_complete else "partial_or_legacy_unknown",
            "unattributed_legacy_invocations_possible": not source_recorded},
        "revisions": [{"input_revision": revision, **aggregate(
            [row for row in accounting if row.input_revision == revision], coverage_complete)} for revision in revisions],
        "as_of": as_of.isoformat() if as_of is not None else None,
        "enforcement": {"scope": "managed_native_admission", "provider_request_cap_enforced": False,
            "token_limit_mode": "observed_after_terminal", "cost_hard_cap_enforced": False,
            "initial_limit_declared_after_native_start": source_recorded}}
    return summary
