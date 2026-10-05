"""Durable exact-attempt cancellation, with authenticated process receipts."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select, update

from anygarden.db.engine import begin_write_transaction
from anygarden.db.models import (
    AgentTurn,
    AgentTurnAttempt,
    AgentTurnOutbox,
    ExecutionMutation,
    ExecutionStop,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Task,
)
from anygarden.project_executions.authorization import SAFE_STOP_STATES

STOP_TIMEOUT_SEC = 45


def _stop_reason(mutation: ExecutionMutation) -> str:
    if mutation.action == "deadline":
        return "EXECUTION_DEADLINE_REACHED"
    if mutation.action == "limit":
        return mutation.reason
    return "execution_input_superseded" if mutation.action == "revise" else "execution_cancelled"


def _now():
    return datetime.now(UTC)


def stop_payload(row: ExecutionStop) -> dict:
    return {"type": "turn_stop", "stop_id": row.id, "agent_id": row.agent_id,
        "room_id": row.room_id, "request_id": row.request_id, "attempt": row.attempt,
        "generation": row.generation, "execution_id": row.execution_id,
        "input_revision": row.input_revision, "local_execution_id": row.local_execution_id,
        "reason": row.reason}


def public_stop(row: ExecutionStop) -> dict:
    result = stop_payload(row)
    result.pop("type")
    result.update(status=row.status, receipt=row.receipt, error_code=row.error_code,
        requested_at=row.requested_at.isoformat(), deadline_at=row.deadline_at.isoformat(),
        confirmed_at=row.confirmed_at.isoformat() if row.confirmed_at else None)
    return result


async def stop_summary(db, execution_id: str) -> dict:
    rows = list(await db.scalars(select(ExecutionStop).where(ExecutionStop.execution_id == execution_id)))
    counts = {key: 0 for key in ("pending", "confirmed", "not_started", "already_finished", "unknown")}
    for row in rows:
        counts["pending" if row.status in {"pending", "delivered"} else row.status] += 1
    return {"total": len(rows), **counts, "all_confirmed": all(row.status in SAFE_STOP_STATES for row in rows)}


async def fence_execution_turns(db, *, execution: ProjectExecution, mutation: ExecutionMutation) -> None:
    """Fence intent/outbox first; do not claim a running process has stopped."""
    from anygarden.turns.service import OPEN_TURN_STATES, mark_turn_terminal

    task_ids = list(await db.scalars(select(Task.id).where(
        Task.execution_id == execution.id, Task.input_revision <= mutation.previous_input_revision,
    )))
    turns = list(await db.scalars(select(AgentTurn).where(
        or_(AgentTurn.execution_id == execution.id, AgentTurn.task_id.in_(task_ids)),
        or_(AgentTurn.state.in_(OPEN_TURN_STATES), select(AgentTurnAttempt.id).where(
            AgentTurnAttempt.turn_id == AgentTurn.request_id,
            AgentTurnAttempt.local_execution_id.is_not(None),
        ).exists()),
    ).with_for_update()))
    for turn in turns:
        attempts = list(await db.scalars(select(AgentTurnAttempt).where(
            AgentTurnAttempt.turn_id == turn.request_id,
            or_(AgentTurnAttempt.state.in_({"pending", "leased", "started", "completing"}),
                AgentTurnAttempt.local_execution_id.is_not(None)),
        ).with_for_update()))
        for attempt in attempts:
            row = await db.scalar(select(ExecutionStop).where(ExecutionStop.attempt_id == attempt.id))
            terminal = await db.scalar(select(ProjectExecutionEvent).where(
                ProjectExecutionEvent.execution_id == execution.id,
                ProjectExecutionEvent.event_key ==
                f"turn:{turn.request_id}:attempt:{attempt.attempt_number}:native-terminal",
            )) if attempt.local_execution_id else None
            proof = terminal.details if terminal and isinstance(terminal.details, dict) else {}
            native_safe = (proof.get("local_execution_id") == attempt.local_execution_id
                           and proof.get("process_state") in {"finished", "stopped", "not_started"})
            if row is None and native_safe:
                # Accepted output or a terminal server attempt alone cannot
                # prove process exit. An authenticated exact native receipt can.
                if attempt.state in {"pending", "leased", "started", "completing"}:
                    attempt.state = "cancelled"
                    attempt.ended_at = attempt.ended_at or _now()
                    attempt.reason = _stop_reason(mutation)
                    attempt.outcome = "cancelled"
                continue
            if row is None:
                # A pending attempt has never crossed the server lease/send
                # commit. Leased attempts need a client receipt even when
                # started_at is NULL: they may already be in the native FIFO.
                not_delivered = attempt.state == "pending" and attempt.local_execution_id is None
                row = ExecutionStop(mutation_id=mutation.id, execution_id=execution.id,
                    input_revision=turn.execution_input_revision or mutation.previous_input_revision,
                    request_id=turn.request_id, attempt_id=attempt.id,
                    attempt=attempt.attempt_number, generation=attempt.generation,
                    agent_id=turn.agent_id, room_id=turn.room_id,
                    participant_id=turn.target_participant_id,
                    local_execution_id=attempt.local_execution_id,
                    reason=_stop_reason(mutation),
                    status="not_started" if not_delivered else "pending",
                    deadline_at=_now() + timedelta(seconds=STOP_TIMEOUT_SEC))
                if not_delivered:
                    row.confirmed_at = _now()
                    row.receipt = {"status": "not_started", "code": "SERVER_ATTEMPT_NOT_LEASED"}
                db.add(row)
            if attempt.state in {"pending", "leased", "started", "completing"}:
                attempt.state = "cancelled"
                attempt.ended_at = attempt.ended_at or _now()
                attempt.reason = row.reason
                attempt.outcome = "cancelled"
        await db.execute(update(AgentTurnOutbox).where(
            AgentTurnOutbox.turn_id == turn.request_id,
            AgentTurnOutbox.state.in_({"pending", "delivering"}),
        ).values(state="cancelled"))
        if turn.state in OPEN_TURN_STATES:
            await mark_turn_terminal(db, turn, state="cancelled",
                reason=_stop_reason(mutation), at=_now())
    await db.flush()


async def settle_execution_mutation(db, execution_id: str) -> bool:
    """Resume a new revision only after every older exact stop is confirmed."""
    from anygarden.messages.service import inject_task_assignment_message
    from anygarden.project_executions.service import (
        ExecutionConflict,
        _event,
        _queue_message,
    )

    execution = await db.get(ProjectExecution, execution_id, populate_existing=True, with_for_update=True)
    if execution is None or execution.status not in {"revising", "cancelling"}:
        return False
    mutation = await db.scalar(select(ExecutionMutation).where(
        ExecutionMutation.execution_id == execution.id,
        ExecutionMutation.phase == "awaiting_stop",
    ).order_by(ExecutionMutation.requested_at.desc(), ExecutionMutation.id).with_for_update())
    if mutation is None or mutation.input_revision != execution.input_revision:
        return False
    if await db.scalar(select(ExecutionStop.id).where(
        ExecutionStop.execution_id == execution.id,
        ExecutionStop.status.not_in(SAFE_STOP_STATES),
    ).limit(1)):
        return False
    expected_status = "revising" if mutation.action == "revise" else "cancelling"
    next_status = ("planning" if mutation.action == "revise" else
                   "failed" if mutation.action in {"deadline", "limit"} else "cancelled")
    message = None
    resume_error = None
    if mutation.action == "revise":
        from anygarden.db.models import Agent, Room
        root = await db.get(Task, mutation.root_task_id, with_for_update=True)
        room = await db.get(Room, execution.operating_room_id, with_for_update=True)
        participant = await db.get(Participant, root.assignee_participant_id, with_for_update=True) if root else None
        agent = await db.get(Agent, execution.lead_agent_id, with_for_update=True)
        if (root is None or root.execution_id != execution.id or root.input_revision != mutation.input_revision
            or participant is None or participant.agent_id != execution.lead_agent_id
            or participant.room_id != execution.operating_room_id or participant.role not in {"member", "admin", "owner"}
            or room is None or room.archived_at is not None
            or agent is None or agent.desired_state != "running"):
            resume_error = "REVISION_LEAD_UNAVAILABLE"
    if resume_error is None:
        try:
            # The safe receipt is flushed outside this savepoint. Losing
            # membership/workspace authority can block a new assignment but
            # cannot roll back the confirmed stop of the previous process.
            async with db.begin_nested():
                changed = await db.scalar(update(ProjectExecution).where(
                    ProjectExecution.id == execution.id,
                    ProjectExecution.status == expected_status,
                    ProjectExecution.input_revision == mutation.input_revision,
                    ~select(ExecutionStop.id).where(ExecutionStop.execution_id == ProjectExecution.id,
                        ExecutionStop.status.not_in(SAFE_STOP_STATES)).exists(),
                ).values(status=next_status, state_revision=ProjectExecution.state_revision + 1,
                    finished_at=_now() if mutation.action != "revise" else None,
                    error=mutation.reason if mutation.action in {"deadline", "limit"} else None,
                    updated_at=_now()).returning(ProjectExecution.id))
                if changed is None:
                    return False
                if mutation.action == "revise":
                    message = await inject_task_assignment_message(db, room=room, task=root,
                        sender_participant_id=None, event="assigned")
                    assigned = await db.scalar(select(AgentTurn).where(AgentTurn.trigger_message_id == message.id,
                        AgentTurn.task_id == root.id))
                    if assigned is None or assigned.state in {"cancelled", "failed"}:
                        raise ExecutionConflict("REVISION_ASSIGNMENT_REVOKED", "The revised assignment is no longer authorized")
        except (ExecutionConflict, HTTPException):
            resume_error = "REVISION_ASSIGNMENT_REVOKED"
    if resume_error is not None:
        await db.execute(update(ProjectExecution).where(ProjectExecution.id == execution_id,
            ProjectExecution.status == "revising", ProjectExecution.input_revision == mutation.input_revision)
            .values(error=resume_error, state_revision=ProjectExecution.state_revision + 1, updated_at=_now()))
        await db.refresh(execution)
        await _event(db, execution, f"mutation:{mutation.id}:resume-blocked:{resume_error}",
            "execution_revision_blocked", task_id=mutation.root_task_id,
            details={"mutation_id": mutation.id, "code": resume_error})
        return False
    if message is not None:
        _queue_message(db, message)
    mutation.phase = "applied"
    mutation.completed_at = _now()
    if mutation.action in {"deadline", "limit"}:
        root = await db.get(Task, mutation.root_task_id)
        if root is not None and root.status != "done":
            root.status = "failed"
            root.error = mutation.reason
            root.finished_at = root.finished_at or _now()
            db.info.setdefault("project_execution_deadline_tasks", set()).add(root.id)
    await _event(db, execution, f"mutation:{mutation.id}:applied",
        "execution_revised" if mutation.action == "revise" else
        "execution_usage_limit_stopped" if mutation.action == "limit" else
        "execution_deadline_stopped" if mutation.action == "deadline" else "execution_cancelled",
        task_id=mutation.root_task_id, details={"mutation_id": mutation.id, "input_revision": mutation.input_revision})
    await db.flush()
    await db.refresh(execution)
    return True


async def record_stop_receipt(db, *, agent_id: str, room_id: str, packet) -> ExecutionStop:
    """Accept the original authenticated target's receipt after lease revocation."""
    from anygarden.project_executions.service import ExecutionConflict, _event
    from anygarden.project_executions.usage import _lock_execution

    body = packet if isinstance(packet, dict) else packet.model_dump()
    # Limit/cancellation fencing owns Execution before Stop. Receipt ingress
    # follows that order too, even for a revoked historical attempt.
    scope = await db.scalar(select(ExecutionStop.execution_id).where(
        ExecutionStop.id == body.get("stop_id"),
    ))
    if scope is None:
        raise ExecutionConflict("STOP_NOT_FOUND", "Exact stop request not found")
    execution = await _lock_execution(db, scope)
    row = await db.get(ExecutionStop, body.get("stop_id"), populate_existing=True, with_for_update=True)
    if row is None or execution is None:
        raise ExecutionConflict("STOP_NOT_FOUND", "Exact stop request not found")
    if row.execution_id != scope:
        raise ExecutionConflict("STOP_IDENTITY_MISMATCH", "Stop scope changed while acquiring its fence")
    identities = {"request_id": row.request_id,
        "attempt": row.attempt, "generation": row.generation, "execution_id": row.execution_id,
        "input_revision": row.input_revision}
    participant = await db.get(Participant, row.participant_id)
    if (agent_id != row.agent_id or room_id != row.room_id
        or participant is None or participant.agent_id != agent_id or participant.room_id != room_id
        or any(body.get(key) != value for key, value in identities.items())):
        raise ExecutionConflict("STOP_IDENTITY_MISMATCH", "Stop receipts must match the authenticated original exact attempt")
    status = body.get("status")
    local_id = body.get("local_execution_id")
    if status not in {*SAFE_STOP_STATES, "unknown"}:
        raise ExecutionConflict("STOP_RECEIPT_INVALID", "A confirmed process outcome or unknown is required")
    if local_id is not None:
        try:
            if str(UUID(local_id)) != local_id:
                raise ValueError
        except (TypeError, ValueError):
            raise ExecutionConflict("STOP_RECEIPT_INVALID", "Local invocation ID must be canonical") from None
    if row.local_execution_id is not None and local_id != row.local_execution_id:
        raise ExecutionConflict("STOP_LOCAL_EXECUTION_MISMATCH", "Receipt must refer to the permitted native invocation")
    if status in {"confirmed", "already_finished"} and local_id is None:
        raise ExecutionConflict("STOP_PROCESS_PROOF_MISSING", "A native process receipt requires its actual local invocation ID")
    expected_state = {"confirmed": "stopped", "not_started": "not_started", "already_finished": "finished", "unknown": "unknown"}[status]
    if body.get("process_state") != expected_state:
        raise ExecutionConflict("STOP_PROCESS_PROOF_INVALID", "Stop status must agree with the native process receipt state")
    receipt = {"status": status, "local_execution_id": local_id}
    for name in ("process_state", "outcome", "code"):
        value = body.get(name)
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", value)):
            raise ExecutionConflict("STOP_RECEIPT_INVALID", "Receipt details must be bounded status codes, never raw native data")
        if value is not None:
            receipt[name] = value
    if row.status in SAFE_STOP_STATES:
        if row.receipt != receipt:
            raise ExecutionConflict("STOP_RECEIPT_CONFLICT", "An accepted process receipt is immutable")
        return row
    changed = await db.scalar(update(ExecutionStop).where(
        ExecutionStop.id == row.id, ExecutionStop.status.in_({"pending", "delivered", "unknown"}),
    ).values(status=status, local_execution_id=local_id, receipt=receipt,
        error_code=body.get("code") if status == "unknown" else None,
        confirmed_at=_now()).returning(ExecutionStop.id))
    if changed is None:
        raise ExecutionConflict("STOP_RECEIPT_CONFLICT", "Stop receipt changed while recording")
    await _event(db, execution, f"stop:{row.id}:receipt:{status}", "execution_stop_receipt",
        details={"stop_id": row.id, "request_id": row.request_id, "status": status, "local_execution_id": local_id})
    await db.refresh(row)
    await settle_execution_mutation(db, row.execution_id)
    return row


async def expire_pending_stops(db, *, now=None) -> int:
    from anygarden.project_executions.service import _event
    now = now or _now()
    rows = list(await db.scalars(select(ExecutionStop).where(
        ExecutionStop.status.in_({"pending", "delivered"}), ExecutionStop.deadline_at <= now,
    ).with_for_update()))
    for row in rows:
        row.status = "unknown"
        row.error_code = "STOP_RECEIPT_TIMEOUT"
        execution = await db.get(ProjectExecution, row.execution_id)
        await _event(db, execution, f"stop:{row.id}:timeout", "execution_stop_unknown",
            details={"stop_id": row.id, "request_id": row.request_id, "code": row.error_code})
    await db.flush()
    return len(rows)


async def deliver_pending_stops(session_factory, manager, *, participant_ids=None, limit: int = 100) -> int:
    """Bounded retry of a durable stop; reconnect delivery never starts work."""
    if manager is None:
        return 0
    from anygarden.ws.protocol import TurnStopOut
    async with session_factory() as db:
        stmt = select(ExecutionStop.id).where(ExecutionStop.status.in_({"pending", "delivered"}),
            ExecutionStop.available_at <= _now(), ExecutionStop.deadline_at > _now()).limit(limit)
        if participant_ids is not None:
            stmt = stmt.where(ExecutionStop.participant_id.in_(set(participant_ids)))
        ids = list(await db.scalars(stmt))
    sent_count = 0
    for stop_id in ids:
        async with session_factory() as db:
            await begin_write_transaction(db)
            row = await db.get(ExecutionStop, stop_id)
            if row is None or row.participant_id is None:
                continue
            changed = await db.scalar(update(ExecutionStop).where(
                ExecutionStop.id == stop_id, ExecutionStop.status.in_({"pending", "delivered"}),
                ExecutionStop.available_at <= _now(), ExecutionStop.deadline_at > _now(),
            ).values(available_at=_now() + timedelta(seconds=5), delivery_count=ExecutionStop.delivery_count + 1)
                .returning(ExecutionStop.id))
            if changed is None:
                continue
            frame = TurnStopOut.model_validate(stop_payload(row))
            participant_id, generation = row.participant_id, row.generation
            await db.commit()
        try:
            sent = await manager.send_to(participant_id, frame, expected_generation=generation)
        except Exception:  # noqa: BLE001 — persistence remains authoritative on disconnect
            sent = False
        if sent:
            sent_count += 1
            async with session_factory() as db:
                await db.execute(update(ExecutionStop).where(ExecutionStop.id == stop_id,
                    ExecutionStop.status == "pending").values(status="delivered", delivered_at=_now()))
                await db.commit()
    return sent_count
