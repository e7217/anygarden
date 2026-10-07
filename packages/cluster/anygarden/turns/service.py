"""Transactional durable-turn state machine.

The room message is the immutable user intent.  ``AgentTurn`` and its first
outbox row are inserted in the same transaction as that message.  Delivery is
at-least-once; the completion CAS is the single gate that makes the visible
reply at-most-once.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.engine import begin_write_transaction
from anygarden.db.models import (
    ActivityLog,
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    AgentTurnOutbox,
    Message,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    Task,
    TaskResult,
)
from anygarden.messages.serialization import message_to_frame
from anygarden.messages.service import append_message
from anygarden.rooms.authorization import AGENT_EXECUTION_ROLES

ACTIVE_ATTEMPT_STATES = frozenset({"leased", "started"})
OPEN_TURN_STATES = frozenset({"pending", "leased", "retrying", "completing"})
DEFAULT_LEASE_SEC = 1200
OUTBOX_RECLAIM_SEC = 30
FAILURE_NOTICE = "⚠️ 에이전트 응답을 복구하지 못해 이 요청을 종료했습니다."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_token() -> str:
    return secrets.token_urlsafe(32)


class _TurnBindingChanged(Exception):
    """One-way source adoption raced an unlocked scope observation."""


async def _lock_scoped_turn(db: AsyncSession, request_id: str):
    """Lock Execution before Turn, without granting any workflow permission.

    A changed NULL binding rolls back the acquisition savepoint, so a caller
    never retains a Turn lock before trying the newly adopted Execution.
    """
    try:
        async with db.begin_nested():
            with db.no_autoflush:
                binding = (await db.execute(select(
                    AgentTurn.execution_id, AgentTurn.execution_input_revision,
                    AgentTurn.task_id,
                ).where(AgentTurn.request_id == request_id))).first()
                if binding is None:
                    return None, False
                if binding.execution_id is not None:
                    await db.execute(update(ProjectExecution).where(
                        ProjectExecution.id == binding.execution_id,
                    ).values(state_revision=ProjectExecution.state_revision,
                             updated_at=ProjectExecution.updated_at))
                turn = await db.get(AgentTurn, request_id, populate_existing=True,
                                    with_for_update=True)
                if (turn is None or (turn.execution_id, turn.execution_input_revision,
                                     turn.task_id) != tuple(binding)):
                    raise _TurnBindingChanged
        return turn, False
    except _TurnBindingChanged:
        return None, True


TERMINAL_TURN_STATES = frozenset({"completed", "cancelled", "failed"})
TerminalTurnState = Literal["completed", "cancelled", "failed"]


async def _on_turn_terminal(db: AsyncSession, turn: AgentTurn) -> None:
    """#762 — extension point fired once per terminal turn transition.

    Settles the async peer fan-in (a peer's answer, or the caller's turn
    closing). It runs inside the caller's transaction after the terminal
    fields are set, so it may only do DB work (no commits, no broadcasts).
    """
    from anygarden.orchestration.peer_fanin import on_turn_terminal

    await on_turn_terminal(db, turn)


async def mark_turn_terminal(
    db: AsyncSession,
    turn: AgentTurn,
    *,
    state: TerminalTurnState,
    reason: str | None,
    at: datetime | None = None,
    attempt: AgentTurnAttempt | None = None,
    attempt_state: TerminalTurnState | None = None,
    attempt_outcome: str | None = None,
    attempt_reason: str | None = None,
) -> None:
    """Move ``turn`` to a terminal state — the single gate for doing so.

    #762 — every path that closes a turn goes through here so
    ``_on_turn_terminal`` fires exactly once per transition. The helper only
    writes what each call site wrote before:

    * ``at`` stamps ``turn.completed_at`` (and ``attempt.ended_at``); it is
      left unset by the delivery-time cancels, which never stamped them.
    * ``attempt`` is updated only when given: its state defaults to
      ``state``; ``attempt_outcome`` / ``attempt_reason`` are written only
      when not ``None``.

    Runs in the caller's session/transaction and never commits.
    """

    if state not in TERMINAL_TURN_STATES:
        raise ValueError(f"not a terminal turn state: {state!r}")
    turn.state = state
    turn.terminal_reason = reason
    if at is not None:
        turn.completed_at = at
    if attempt is not None:
        attempt.state = attempt_state or state
        if at is not None:
            attempt.ended_at = at
        if attempt_outcome is not None:
            attempt.outcome = attempt_outcome
        if attempt_reason is not None:
            attempt.reason = attempt_reason
    await _on_turn_terminal(db, turn)


async def create_turn(
    db: AsyncSession,
    *,
    room_id: str,
    participant_id: str,
    agent_id: str,
    trigger_message_id: str,
    thread_root_id: str | None = None,
    task_id: str | None = None,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    retry_count: int = 0,
    max_retries: int = 1,
) -> AgentTurn:
    """Insert a Turn, attempt, and outbox row in the caller transaction."""

    rid = request_id or str(uuid4())
    key = (
        idempotency_key or f"message:{trigger_message_id}:participant:{participant_id}"
    )
    existing = (
        await db.execute(select(AgentTurn).where(AgentTurn.idempotency_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    task = None
    if task_id is not None:
        with db.no_autoflush:
            binding = (await db.execute(select(Task.execution_id, Task.input_revision)
                .where(Task.id == task_id))).first()
            if binding is not None and binding.execution_id is not None:
                await db.execute(update(ProjectExecution).where(
                    ProjectExecution.id == binding.execution_id,
                ).values(state_revision=ProjectExecution.state_revision,
                         updated_at=ProjectExecution.updated_at))
        task = await db.get(Task, task_id, populate_existing=True,
                            with_for_update=binding is not None and binding.execution_id is not None)
        if ((binding is not None and (task is None or
            (task.execution_id, task.input_revision) != tuple(binding)))
            or (binding is None and task is not None and task.execution_id is not None)):
            from anygarden.project_executions.service import ExecutionConflict

            raise ExecutionConflict("TURN_EXECUTION_BINDING_CHANGED",
                                    "Task execution binding changed before its new intent")

    agent = await db.get(Agent, agent_id)
    generation = int(agent.generation or 0) if agent is not None else 0
    state = "pending"
    reason: str | None = None
    if agent is None or agent.desired_state != "running":
        state = "cancelled"
        reason = "agent_not_running"

    # #762 — the row is always built open; a turn born terminal is closed
    # through ``mark_turn_terminal`` right after it is added (below).
    turn = AgentTurn(
        request_id=rid,
        room_id=room_id,
        target_participant_id=participant_id,
        agent_id=agent_id,
        trigger_message_id=trigger_message_id,
        thread_root_id=thread_root_id,
        task_id=task_id,
        idempotency_key=key,
        state="pending",
        active_attempt=1,
        retry_count=retry_count,
        max_retries=max_retries,
    )
    # Phase 5 — an active external-workspace lease turns every invocation
    # into an epoch-bound intent. Cross-room turns and write turns without a
    # claimed, source-linked task are cancelled before any outbox is created.
    from anygarden.workspaces.service import bind_turn

    trigger_message = await db.get(Message, trigger_message_id)
    workspace_allowed, workspace_reason = await bind_turn(
        db, turn=turn, message=trigger_message
    )
    if state == "pending" and not workspace_allowed:
        state = "cancelled"
        reason = workspace_reason or "workspace_authorization_revoked"
    if task is not None and task.execution_id is not None:
        turn.execution_id = task.execution_id
        turn.execution_input_revision = task.input_revision
    execution_ok, execution_reason, _ = await _execution_gate(db, turn, lock=True)
    if state == "pending" and not execution_ok:
        state = "cancelled"
        reason = execution_reason or "execution_authorization_revoked"
    if state == "pending" and turn.execution_id is not None:
        admission = await _execution_admission(db, turn)
        if not admission.allowed:
            state = "cancelled"
            reason = admission.reason_code or "EXECUTION_ADMISSION_DENIED"
    db.add(turn)
    if state == "cancelled":
        await mark_turn_terminal(db, turn, state="cancelled", reason=reason)
    attempt = AgentTurnAttempt(
        id=str(uuid4()),
        turn_id=rid,
        agent_id=agent_id,
        attempt_number=1,
        generation=generation,
        lease_token=_lease_token(),
        state="pending" if state == "pending" else "cancelled",
        reason=reason,
    )
    db.add(attempt)
    if state == "pending":
        db.add(
            AgentTurnOutbox(
                id=str(uuid4()),
                turn_id=rid,
                attempt_id=attempt.id,
                room_id=room_id,
                participant_id=participant_id,
                state="pending",
            )
        )
    await db.flush()
    return turn


async def active_lease_count(
    db: AsyncSession, *, agent_id: str, generation: int | None = None
) -> int:
    stmt = (
        select(func.count())
        .select_from(AgentTurnAttempt)
        .where(
            AgentTurnAttempt.agent_id == agent_id,
            AgentTurnAttempt.state.in_(ACTIVE_ATTEMPT_STATES),
        )
    )
    if generation is not None:
        stmt = stmt.where(AgentTurnAttempt.generation == generation)
    return int((await db.scalar(stmt)) or 0)


async def _workspace_gate(
    db: AsyncSession, turn: AgentTurn
) -> tuple[bool, str | None, Any]:
    from anygarden.workspaces.service import validate_turn

    return await validate_turn(db, turn)


async def _execution_gate(
    db: AsyncSession, turn: AgentTurn, *, lock: bool = False,
    completion_attempt: AgentTurnAttempt | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    from anygarden.project_executions.authorization import validate_execution_turn

    allowed, reason, snapshot = await validate_execution_turn(db, turn, lock=lock)
    if allowed or completion_attempt is None or reason != "EXECUTION_NOT_ACTIVE":
        return allowed, reason, snapshot
    # A finalizing MCP operation commits the accepted result before the CLI
    # emits its visible acknowledgement. Only that exact producer attempt may
    # finish a completed execution. Cancellation and a newer input never grant
    # this exception, and dispatch/native start never pass completion_attempt.
    accepted = select(TaskResult.id).join(Task, Task.id == TaskResult.task_id).where(
        TaskResult.execution_id == turn.execution_id,
        TaskResult.task_id == turn.task_id,
        TaskResult.input_revision == turn.execution_input_revision,
        TaskResult.attempt_id == completion_attempt.id,
        TaskResult.producer_agent_id == turn.agent_id,
        Task.status == "done",
        Task.execution_id == turn.execution_id,
        Task.input_revision == turn.execution_input_revision,
    ).exists()
    stmt = update(ProjectExecution).where(
        ProjectExecution.id == turn.execution_id,
        ProjectExecution.input_revision == turn.execution_input_revision,
        ProjectExecution.status == "completed", accepted,
    ).values(state_revision=ProjectExecution.state_revision).returning(ProjectExecution.id)
    if await db.scalar(stmt) is not None:
        return True, None, None
    return False, reason, None


async def _execution_admission(db: AsyncSession, turn: AgentTurn, *,
                               attempt: AgentTurnAttempt | None = None):
    """Fence new intents, keeping exact permitted transport replay unchanged.

    Call only after current execution/participant authorization. Completion,
    publication and historical accounting never use this admission gate.
    """
    from anygarden.project_executions.usage import (
        AdmissionDisposition,
        admission_disposition,
        is_permitted_native_invocation,
    )

    if turn.execution_id is None:
        return AdmissionDisposition(True)
    if attempt is not None and await is_permitted_native_invocation(db, turn=turn, attempt=attempt):
        return AdmissionDisposition(True)
    execution = await db.get(ProjectExecution, turn.execution_id, populate_existing=True)
    if execution is None or execution.input_revision != turn.execution_input_revision:
        return AdmissionDisposition(False, reason_code="EXECUTION_INPUT_SUPERSEDED")
    admission = await admission_disposition(db, execution=execution, phase="intent")
    if not admission.allowed and not admission.wait and admission.reason_code in {
        "EXECUTION_NATIVE_INVOCATION_LIMIT", "EXECUTION_USAGE_LIMIT_REACHED",
    }:
        # Denial evidence deliberately survives a surrounding SQL savepoint.
        # Actual closure occurs after the outer commit/rollback, in a fresh
        # authorized transaction which rechecks the canonical budget.
        db.info.setdefault("project_execution_usage_denials", {})[execution.id] = admission.reason_code
    return admission


async def _is_operating_lead(db: AsyncSession, turn: AgentTurn) -> bool:
    """#802 — the turn's agent leads a room that has active subrooms.

    The room's representative is filled with its first agent automatically,
    so being the representative alone does not make a room an operating
    room; delegating to subrooms needs subrooms to exist.
    """
    room = await db.get(Room, turn.room_id)
    if room is None or room.is_dm or room.representative_agent_id != turn.agent_id:
        return False
    subroom = await db.scalar(
        select(Room.id)
        .where(Room.parent_room_id == room.id, Room.archived_at.is_(None))
        .limit(1)
    )
    return subroom is not None


def _durable_metadata(
    turn: AgentTurn, attempt: AgentTurnAttempt, base: dict[str, Any] | None,
    *, input_snapshot: dict[str, Any] | None = None, operating_lead: bool = False,
) -> dict[str, Any]:
    metadata = dict(base or {})
    metadata.pop("operating_lead", None)
    if operating_lead:
        metadata["operating_lead"] = True
    metadata.update(
        {
            "request_id": turn.request_id,
            "turn_attempt": attempt.attempt_number,
            "turn_generation": attempt.generation,
            "turn_lease": attempt.lease_token,
            "turn_idempotency_key": turn.idempotency_key,
            "turn_protocol": 1,
        }
    )
    # Never trust an old message's execution metadata after an input change.
    for key in ("execution_id", "input_revision", "execution_input_snapshot"):
        metadata.pop(key, None)
    if turn.execution_id is not None:
        metadata.update(execution_id=turn.execution_id,
                        input_revision=turn.execution_input_revision,
                        execution_input_snapshot=input_snapshot)
        if turn.task_id is not None:
            # A continuation can use an ingest-only human notice as its
            # trigger. Only this leased delivery gains the authoritative task
            # identity; leave the persisted notice and its rich data intact.
            existing_assignment = metadata.get("task_assignment")
            assignment = dict(existing_assignment) if isinstance(existing_assignment, dict) else {}
            assignment.update(task_id=turn.task_id, assignee_pid=turn.target_participant_id)
            metadata["task_assignment"] = assignment
    if turn.workspace_attachment_id is not None:
        metadata.update(
            {
                "workspace_attachment_id": turn.workspace_attachment_id,
                "workspace_attachment_epoch": turn.workspace_attachment_epoch,
            }
        )
    return metadata


async def deliver_pending_outbox(
    session_factory: Any,
    manager: Any,
    *,
    participant_ids: Iterable[str] | None = None,
    limit: int = 100,
    app: Any = None,
) -> int:
    """Deliver currently due outbox rows to matching live subscriptions.

    A short ``available_at`` claim prevents two workers from sending the same
    row concurrently.  The row remains pending until the socket write succeeds,
    so a server crash between claim and send is reclaimed automatically.
    """

    now = _now()
    pids = set(participant_ids or ())
    async with session_factory() as db:
        stmt = (
            select(AgentTurnOutbox.id)
            .where(
                AgentTurnOutbox.state == "pending",
                AgentTurnOutbox.available_at <= now,
            )
            .order_by(AgentTurnOutbox.created_at)
            .limit(limit)
        )
        if pids:
            stmt = stmt.where(AgentTurnOutbox.participant_id.in_(pids))
        outbox_ids = list((await db.scalars(stmt)).all())

    delivered = 0
    for outbox_id in outbox_ids:
        async with session_factory() as db:
            await begin_write_transaction(db)
            row = await db.get(AgentTurnOutbox, outbox_id)
            if (
                row is None
                or row.state != "pending"
                or row.available_at > now
                or row.participant_id is None
            ):
                continue
            turn = await db.get(AgentTurn, row.turn_id)
            attempt = await db.get(AgentTurnAttempt, row.attempt_id)
            if turn is None or attempt is None or turn.state not in OPEN_TURN_STATES:
                row.state = "cancelled"
                await db.commit()
                continue
            turn, changed_binding = await _lock_scoped_turn(db, row.turn_id)
            if changed_binding or turn is None:
                await db.rollback()
                continue
            attempt = await db.scalar(select(AgentTurnAttempt).where(
                AgentTurnAttempt.id == row.attempt_id,
                AgentTurnAttempt.turn_id == turn.request_id,
            ).execution_options(populate_existing=True).with_for_update())
            if attempt is None or attempt.turn_id != turn.request_id:
                await db.rollback()
                continue
            execution_ok, execution_reason, input_snapshot = await _execution_gate(
                db, turn, lock=True
            )
            if not execution_ok:
                reason = execution_reason or "execution_authorization_revoked"
                await mark_turn_terminal(db, turn, state="cancelled", reason=reason,
                                         attempt=attempt, attempt_reason=reason)
                row.state, row.last_error = "cancelled", reason
                await db.commit()
                continue
            (
                workspace_ok,
                workspace_reason,
                workspace_attachment,
            ) = await _workspace_gate(db, turn)
            if not workspace_ok:
                reason = workspace_reason or "workspace_authorization_revoked"
                await mark_turn_terminal(
                    db,
                    turn,
                    state="cancelled",
                    reason=reason,
                    attempt=attempt,
                    attempt_reason=reason,
                )
                row.state = "cancelled"
                row.last_error = reason
                if workspace_attachment is not None:
                    from anygarden.workspaces.service import append_audit

                    await append_audit(
                        db,
                        attachment=workspace_attachment,
                        event_type="dispatch_denied",
                        request_id=turn.request_id,
                        task_id=turn.task_id,
                        source_message_id=turn.trigger_message_id,
                        source_thread_root_id=turn.thread_root_id,
                        outcome="denied",
                        details={"reason": reason},
                    )
                await db.commit()
                continue
            joined = (
                await db.execute(
                    select(Participant, Room, Agent)
                    .join(Room, Room.id == Participant.room_id)
                    .join(Agent, Agent.id == Participant.agent_id)
                    .where(
                        Participant.id == row.participant_id,
                        Participant.room_id == row.room_id,
                        Participant.agent_id == turn.agent_id,
                        Participant.role.in_(AGENT_EXECUTION_ROLES),
                        Room.archived_at.is_(None),
                    )
                )
            ).first()
            if joined is None or joined[2].desired_state != "running":
                await mark_turn_terminal(
                    db,
                    turn,
                    state="cancelled",
                    reason="authorization_revoked",
                    attempt=attempt,
                    attempt_reason="authorization_revoked",
                )
                row.state = "cancelled"
                row.last_error = "authorization_revoked"
                db.add(
                    ActivityLog(
                        agent_id=turn.agent_id,
                        event_type="turn_cancelled",
                        request_id=turn.request_id,
                        room_id=turn.room_id,
                        details={
                            "reason": "authorization_revoked",
                            "attempt": attempt.attempt_number,
                        },
                    )
                )
                await db.commit()
                continue
            agent = joined[2]
            # A retry created for a pending generation must not leak to the old
            # process during the drain/kill hand-off.
            if attempt.generation != int(agent.generation or 0):
                continue
            msg = await db.get(Message, turn.trigger_message_id)
            if msg is None:
                row.state = "cancelled"
                await mark_turn_terminal(
                    db,
                    turn,
                    state="cancelled",
                    reason="trigger_message_deleted",
                    attempt=attempt,
                    attempt_reason="trigger_message_deleted",
                )
                await db.commit()
                continue

            # Preserve room-local user intent order across delivery retries.
            # A failed socket write moves A's outbox availability into the
            # future; without this fence, a still-due B would otherwise lease
            # and execute first. Message.seq is authoritative inside a room,
            # with turn creation order only as a legacy/deletion fallback.
            turn_created_before = or_(
                AgentTurn.created_at < turn.created_at,
                and_(
                    AgentTurn.created_at == turn.created_at,
                    AgentTurn.request_id < turn.request_id,
                ),
            )
            prior_open = await db.scalar(
                select(AgentTurn.request_id)
                .outerjoin(Message, Message.id == AgentTurn.trigger_message_id)
                .where(
                    AgentTurn.request_id != turn.request_id,
                    AgentTurn.room_id == turn.room_id,
                    AgentTurn.target_participant_id == turn.target_participant_id,
                    AgentTurn.state.in_(OPEN_TURN_STATES),
                    or_(
                        Message.seq < msg.seq,
                        and_(Message.seq == msg.seq, turn_created_before),
                        and_(Message.seq.is_(None), turn_created_before),
                    ),
                )
                .limit(1)
            )
            if prior_open is not None:
                continue
            connected = await manager.is_connected(row.participant_id)
            if not connected:
                continue
            if turn.execution_id is not None:
                supports_control = getattr(manager, "participant_turn_control", None)
                if supports_control is None or not await supports_control(row.participant_id):
                    reason = "NATIVE_TURN_CONTROL_REQUIRED"
                    await mark_turn_terminal(db, turn, state="cancelled", reason=reason,
                                             attempt=attempt, attempt_reason=reason)
                    row.state, row.last_error = "cancelled", reason
                    await db.commit()
                    continue
                admission = await _execution_admission(db, turn, attempt=attempt)
                if not admission.allowed:
                    # A budget wait/denial consumes no lease, native UUID or
                    # retry. Keep the original pending intent until the
                    # canonical limit closure or a later allowed dispatch.
                    row.available_at = now + timedelta(seconds=5)
                    row.last_error = admission.reason_code or "EXECUTION_ADMISSION_PENDING"
                    denials = dict(db.info.pop("project_execution_usage_denials", {}))
                    await db.commit()
                    if denials and app is not None:
                        from anygarden.project_executions.limits import (
                            apply_queued_usage_denials,
                        )

                        await apply_queued_usage_denials(app, denials=denials)
                    continue
            subscription_generation = await manager.participant_generation(
                row.participant_id
            )
            legacy = subscription_generation is None
            if not legacy and subscription_generation != attempt.generation:
                continue
            metadata = dict(msg.extra_metadata or {})
            if legacy:
                if turn.execution_id is not None:
                    row.last_error = "PROJECT_TURN_START_PROTOCOL_REQUIRED"
                    await db.commit()
                    continue
                # Mixed rollout: deliver once using the pre-Phase-4 contract,
                # accept at most one legacy completion, and never auto-retry it.
                metadata["request_id"] = turn.request_id
                turn.protocol_version = 0
            else:
                metadata = _durable_metadata(
                    turn, attempt, metadata, input_snapshot=input_snapshot,
                    operating_lead=await _is_operating_lead(db, turn),
                )
            frame = message_to_frame(msg, metadata=metadata)
            participant_id = row.participant_id
            turn_id = row.turn_id
            attempt_id = row.attempt_id
            expected_generation = attempt.generation
            lease_seconds = max(
                60, int(getattr(agent, "turn_timeout_sec", 0) or 900) + 300
            )
            if attempt.state not in {"pending", *ACTIVE_ATTEMPT_STATES}:
                row.state = "cancelled"
                await db.commit()
                continue

            # Claim the outbox and execution lease in one transaction. The
            # reads above are repeated by every contender; this CAS is the
            # single winner gate before any socket side effect.
            claim = await db.execute(
                update(AgentTurnOutbox)
                .where(
                    AgentTurnOutbox.id == outbox_id,
                    AgentTurnOutbox.state == "pending",
                    AgentTurnOutbox.available_at <= now,
                )
                .values(
                    available_at=now + timedelta(seconds=OUTBOX_RECLAIM_SEC),
                    delivery_count=AgentTurnOutbox.delivery_count + 1,
                )
            )
            if claim.rowcount != 1:
                await db.rollback()
                continue
            # Reserve the execution lease before touching the socket. This CAS
            # serializes dispatch with generation changes: a config restart
            # either observes an active lease and drains it, or updates a still-
            # pending attempt before this worker can send it.
            if attempt.state == "pending":
                lease_now = _now()
                leased = await db.execute(
                    update(AgentTurnAttempt)
                    .where(
                        AgentTurnAttempt.id == attempt.id,
                        AgentTurnAttempt.state == "pending",
                        AgentTurnAttempt.generation == expected_generation,
                    )
                    .values(
                        state="leased",
                        leased_at=lease_now,
                        lease_expires_at=lease_now + timedelta(seconds=lease_seconds),
                    )
                )
                if leased.rowcount != 1:
                    await db.rollback()
                    continue
                if turn.state in {"pending", "retrying"}:
                    turn.state = "leased"
            await db.commit()

        sent = await manager.send_to(
            participant_id,
            frame,
            expected_generation=None if legacy else expected_generation,
        )
        if not sent:
            continue

        lease_now = _now()
        async with session_factory() as db:
            await begin_write_transaction(db)
            row2 = await db.get(AgentTurnOutbox, outbox_id)
            turn2 = await db.get(AgentTurn, turn_id)
            attempt2 = await db.get(AgentTurnAttempt, attempt_id)
            if (row2 is None or turn2 is None or attempt2 is None
                or row2.state != "pending"
                or turn2.state not in OPEN_TURN_STATES
                or attempt2.state not in ACTIVE_ATTEMPT_STATES):
                continue
            row2.state = "delivered"
            row2.delivered_at = lease_now
            db.add(
                ActivityLog(
                    agent_id=turn2.agent_id,
                    event_type="turn_dispatched",
                    request_id=turn2.request_id,
                    room_id=turn2.room_id,
                    details={
                        "attempt": attempt2.attempt_number,
                        "generation": attempt2.generation,
                        "legacy": legacy,
                    },
                )
            )
            await db.commit()
        delivered += 1
    return delivered


@dataclass(slots=True)
class CompletionDecision:
    outcome: Literal["legacy", "accept", "failure", "idempotent", "stale"]
    turn: AgentTurn | None = None
    attempt: AgentTurnAttempt | None = None
    existing_message_id: str | None = None
    reason: str | None = None


async def _audit_stale(
    db: AsyncSession,
    *,
    turn: AgentTurn,
    agent_id: str,
    reason: str,
    attempt_number: int | None,
    generation: int | None,
) -> None:
    db.add(
        ActivityLog(
            agent_id=agent_id,
            event_type="stale_completion",
            request_id=turn.request_id,
            room_id=turn.room_id,
            details={
                "reason": reason,
                "attempt": attempt_number,
                "generation": generation,
                "active_attempt": turn.active_attempt,
            },
        )
    )


async def begin_completion(
    db: AsyncSession,
    *,
    request_id: str | None,
    room_id: str,
    participant_id: str,
    agent_id: str,
    attempt_number: int | None,
    generation: int | None,
    lease_token: str | None,
    reply_outcome: str | None = None,
) -> CompletionDecision:
    """Reserve the one user-visible completion before appending its message."""

    if not request_id:
        return CompletionDecision("legacy")
    turn, changed_binding = await _lock_scoped_turn(db, request_id)
    if changed_binding:
        return CompletionDecision("stale", reason="TURN_EXECUTION_BINDING_CHANGED")
    if turn is None:
        return CompletionDecision("legacy")
    workspace_ok, workspace_reason, workspace_attachment = await _workspace_gate(
        db, turn
    )
    if not workspace_ok:
        reason = workspace_reason or "workspace_authorization_revoked"
        await _audit_stale(
            db,
            turn=turn,
            agent_id=agent_id,
            reason=reason,
            attempt_number=attempt_number,
            generation=generation,
        )
        if workspace_attachment is not None:
            from anygarden.workspaces.service import append_audit

            await append_audit(
                db,
                attachment=workspace_attachment,
                event_type="completion_denied",
                request_id=turn.request_id,
                task_id=turn.task_id,
                source_message_id=turn.trigger_message_id,
                source_thread_root_id=turn.thread_root_id,
                outcome="stale",
                details={"reason": reason},
            )
        return CompletionDecision("stale", turn=turn, reason=reason)
    gate = (
        await db.execute(
            select(Participant.id)
            .join(Room, Room.id == Participant.room_id)
            .join(Agent, Agent.id == Participant.agent_id)
            .where(
                Participant.id == participant_id,
                Participant.room_id == room_id,
                Participant.agent_id == agent_id,
                Participant.role.in_(AGENT_EXECUTION_ROLES),
                Room.archived_at.is_(None),
                Agent.desired_state == "running",
            )
        )
    ).scalar_one_or_none()
    if gate is None or turn.room_id != room_id or turn.agent_id != agent_id:
        await _audit_stale(
            db,
            turn=turn,
            agent_id=agent_id,
            reason="authorization_revoked",
            attempt_number=attempt_number,
            generation=generation,
        )
        return CompletionDecision("stale", turn=turn, reason="authorization_revoked")

    attempt = (
        await db.execute(
            select(AgentTurnAttempt).where(
                AgentTurnAttempt.turn_id == turn.request_id,
                AgentTurnAttempt.attempt_number == turn.active_attempt,
            )
        )
    ).scalar_one_or_none()
    if attempt is None:
        return CompletionDecision("stale", turn=turn, reason="attempt_missing")

    legacy = attempt_number is None and generation is None and lease_token is None
    if legacy:
        if turn.retry_count != 0 or turn.active_attempt != 1:
            await _audit_stale(
                db,
                turn=turn,
                agent_id=agent_id,
                reason="legacy_after_redispatch",
                attempt_number=None,
                generation=None,
            )
            return CompletionDecision(
                "stale", turn=turn, reason="legacy_after_redispatch"
            )
    elif (
        attempt_number != attempt.attempt_number
        or generation != attempt.generation
        or lease_token != attempt.lease_token
    ):
        await _audit_stale(
            db,
            turn=turn,
            agent_id=agent_id,
            reason="lease_mismatch",
            attempt_number=attempt_number,
            generation=generation,
        )
        return CompletionDecision("stale", turn=turn, reason="lease_mismatch")

    # Idempotency is granted only to the same authorized agent presenting
    # the active attempt's proof. Checking terminal state before those gates
    # would let a different room agent suppress its own message by replaying a
    # completed request id.
    if turn.accepted_message_id is not None or turn.state == "completed":
        return CompletionDecision(
            "idempotent", turn=turn, existing_message_id=turn.accepted_message_id
        )

    execution_ok, execution_reason, _ = await _execution_gate(
        db, turn, lock=True, completion_attempt=attempt
    )
    if not execution_ok:
        reason = execution_reason or "execution_authorization_revoked"
        await _audit_stale(db, turn=turn, agent_id=agent_id, reason=reason,
                           attempt_number=attempt_number, generation=generation)
        return CompletionDecision("stale", turn=turn, reason=reason)

    if turn.execution_id is not None and reply_outcome in {
        "failed", "timeout", "retry_exhausted", "rejected",
    }:
        # A handler's failure notice is not the accepted result of the task.
        # Keep its completion CAS available for the next bounded attempt.
        # Native termination and retry eligibility come from the authenticated
        # lifecycle receipt, rather than from the text of this notice.
        if turn.state not in {"pending", "leased", "retrying"}:
            return CompletionDecision("stale", turn=turn, reason="failure_notice_after_terminal")
        notice_key = f"turn:{turn.request_id}:attempt:{attempt.attempt_number}:failure-notice"
        existing = await db.scalar(select(ProjectExecutionEvent).where(
            ProjectExecutionEvent.event_key == notice_key,
        ))
        if existing is not None:
            return CompletionDecision("idempotent", turn=turn,
                                      existing_message_id=existing.message_id)
        db.add(ProjectExecutionEvent(
            execution_id=turn.execution_id, task_id=turn.task_id,
            event_key=notice_key, event_type="task_failure_notice",
            details={"attempt": attempt.attempt_number, "outcome": reply_outcome},
        ))
        await db.flush()
        return CompletionDecision("failure", turn=turn, attempt=attempt)

    reserved = await db.execute(
        update(AgentTurn)
        .where(
            AgentTurn.request_id == turn.request_id,
            AgentTurn.state.in_(["pending", "leased", "retrying"]),
            AgentTurn.active_attempt == attempt.attempt_number,
            AgentTurn.accepted_message_id.is_(None),
        )
        .values(state="completing", updated_at=_now())
    )
    if reserved.rowcount != 1:
        await db.refresh(turn)
        if turn.accepted_message_id is not None or turn.state == "completed":
            return CompletionDecision(
                "idempotent", turn=turn, existing_message_id=turn.accepted_message_id
            )
        await _audit_stale(
            db,
            turn=turn,
            agent_id=agent_id,
            reason="completion_cas_lost",
            attempt_number=attempt_number,
            generation=generation,
        )
        return CompletionDecision("stale", turn=turn, reason="completion_cas_lost")
    if legacy:
        turn.protocol_version = 0
        db.add(
            ActivityLog(
                agent_id=agent_id,
                event_type="legacy_completion_accepted",
                request_id=turn.request_id,
                room_id=room_id,
                details={"attempt": 1},
            )
        )
    attempt.state = "completing"
    return CompletionDecision("accept", turn=turn, attempt=attempt)


async def finish_failure_notice(
    db: AsyncSession, *, turn: AgentTurn, attempt: AgentTurnAttempt, message_id: str,
) -> None:
    event = await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.event_key ==
        f"turn:{turn.request_id}:attempt:{attempt.attempt_number}:failure-notice",
    ))
    if event is not None and event.message_id is None:
        event.message_id = message_id


async def finish_completion(
    db: AsyncSession,
    *,
    turn: AgentTurn,
    attempt: AgentTurnAttempt,
    message_id: str,
) -> None:
    now = _now()
    turn.accepted_message_id = message_id
    turn.updated_at = now
    # A visible reply never set a terminal reason; keep the (always ``None``
    # after the completion CAS) value exactly as it is.
    await mark_turn_terminal(
        db,
        turn,
        state="completed",
        reason=turn.terminal_reason,
        at=now,
        attempt=attempt,
        attempt_outcome="ok",
    )
    db.add(
        ActivityLog(
            agent_id=turn.agent_id,
            event_type="response_sent",
            request_id=turn.request_id,
            room_id=turn.room_id,
            details={
                "room_id": turn.room_id,
                "attempt": attempt.attempt_number,
                "generation": attempt.generation,
                "message_id": message_id,
            },
        )
    )
    workspace_ok, _, workspace_attachment = await _workspace_gate(db, turn)
    if workspace_ok and workspace_attachment is not None:
        from anygarden.workspaces.service import append_audit

        await append_audit(
            db,
            attachment=workspace_attachment,
            event_type="invocation_completed",
            request_id=turn.request_id,
            task_id=turn.task_id,
            source_message_id=turn.trigger_message_id,
            source_thread_root_id=turn.thread_root_id,
            outcome="ok",
            changed_count=0,
            details={"attempt": attempt.attempt_number},
        )


async def finish_deferred(
    db: AsyncSession,
    *,
    turn: AgentTurn,
    attempt: AgentTurnAttempt,
) -> None:
    """Close a turn whose reply was kept as an ``ask_peer`` draft (#762).

    The turn completed but posted nothing; the caller is woken again by the
    peer fan-in, so no failure notice and no redispatch apply.
    """
    now = _now()
    turn.updated_at = now
    await mark_turn_terminal(
        db,
        turn,
        state="completed",
        reason="awaiting_peers",
        at=now,
        attempt=attempt,
        attempt_outcome="ok",
    )
    db.add(
        ActivityLog(
            agent_id=turn.agent_id,
            event_type="response_deferred",
            request_id=turn.request_id,
            room_id=turn.room_id,
            details={
                "room_id": turn.room_id,
                "attempt": attempt.attempt_number,
                "generation": attempt.generation,
                "reason": "awaiting_peers",
            },
        )
    )


async def _native_terminal_receipt(
    db: AsyncSession, *, turn: AgentTurn, attempt: AgentTurnAttempt, frame: Any,
) -> tuple[bool, dict | None]:
    """Store one exact invocation receipt without trusting notice text."""
    if turn.execution_id is None:
        return True, None
    names = ("local_execution_id", "native_process_state", "native_outcome")
    values = [getattr(frame, name, None) for name in names]
    if all(value is None for value in values):
        return True, None
    local_id, process_state, outcome = values
    reason_code = getattr(frame, "native_reason_code", None)
    categories = {
        "MODEL_CONFIGURATION_INVALID", "AUTHENTICATION_FAILED",
        "MODEL_TRANSIENT_FAILURE", "MODEL_EXECUTION_FAILED", "PROCESS_OUTCOME_UNKNOWN",
    }
    valid_pair = (
        (outcome == "succeeded" and process_state == "finished")
        or (outcome == "failed" and process_state in {"finished", "stopped", "not_started"})
        or (outcome == "cancelled" and process_state in {"stopped", "not_started"})
        or (outcome == "unknown" and process_state in {
            "unknown", "stopped", "finished", "not_started",
        })
    )
    if (frame.event not in {"engine_call_finished", "handler_finished"}
        or not local_id or local_id != attempt.local_execution_id
        or not valid_pair or reason_code not in categories | {None}
        or (outcome in {"failed", "unknown"} and reason_code is None)):
        return False, None
    details = {
        "local_execution_id": local_id, "process_state": process_state,
        "outcome": outcome, "reason_code": reason_code,
        "retryable": outcome == "failed" and process_state in {
            "finished", "stopped", "not_started",
        },
        "transient": bool(outcome == "failed"
                          and getattr(frame, "native_transient", False)
                          and reason_code == "MODEL_TRANSIENT_FAILURE"),
    }
    key = f"turn:{turn.request_id}:attempt:{attempt.attempt_number}:native-terminal"
    existing = await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.event_key == key,
    ))
    if existing is not None:
        return existing.details == details, existing.details
    db.add(ProjectExecutionEvent(
        execution_id=turn.execution_id, task_id=turn.task_id,
        event_key=key, event_type="task_native_terminal", details=details,
    ))
    await db.flush()
    return True, details


async def record_lifecycle(
    db: AsyncSession,
    *,
    agent_id: str,
    frame: Any,
) -> bool:
    """Validate and apply a lifecycle frame; return False when fenced."""

    turn, changed_binding = await _lock_scoped_turn(db, frame.request_id)
    if changed_binding:
        return False
    if turn is None:
        return True
    attempt_number = getattr(frame, "turn_attempt", None)
    generation = getattr(frame, "turn_generation", None)
    lease_token = getattr(frame, "turn_lease", None)
    legacy = attempt_number is None and generation is None and lease_token is None
    attempt = (
        await db.execute(
            select(AgentTurnAttempt).where(
                AgentTurnAttempt.turn_id == turn.request_id,
                AgentTurnAttempt.attempt_number == turn.active_attempt,
            )
        )
    ).scalar_one_or_none()
    gate = (
        await db.execute(
            select(Participant.id)
            .join(Room, Room.id == Participant.room_id)
            .join(Agent, Agent.id == Participant.agent_id)
            .where(
                Participant.id == turn.target_participant_id,
                Participant.room_id == turn.room_id,
                Participant.agent_id == agent_id,
                Participant.role.in_(AGENT_EXECUTION_ROLES),
                Room.archived_at.is_(None),
                Agent.desired_state == "running",
            )
        )
    ).scalar_one_or_none()
    workspace_ok, workspace_reason, workspace_attachment = await _workspace_gate(
        db, turn
    )
    execution_ok, execution_reason, _ = await _execution_gate(
        db, turn, lock=True,
        completion_attempt=attempt if frame.event in {
            "handler_finished", "engine_call_finished",
        } else None,
    )
    valid = (
        attempt is not None
        and gate is not None
        and workspace_ok
        and execution_ok
        and turn.state not in {"cancelled", "failed"}
        and turn.agent_id == agent_id
        and turn.room_id == frame.room_id
        and (
            (legacy and turn.retry_count == 0 and turn.active_attempt == 1)
            or (
                attempt_number == attempt.attempt_number
                and generation == attempt.generation
                and lease_token == attempt.lease_token
            )
        )
    )
    if not valid:
        await _audit_stale(
            db,
            turn=turn,
            agent_id=agent_id,
            reason=(
                workspace_reason
                or execution_reason
                or (
                    "lifecycle_authorization_revoked"
                    if gate is None
                    else "lifecycle_lease_mismatch"
                )
            ),
            attempt_number=attempt_number,
            generation=generation,
        )
        if workspace_attachment is not None and not workspace_ok:
            from anygarden.workspaces.service import append_audit

            await append_audit(
                db,
                attachment=workspace_attachment,
                event_type="lifecycle_denied",
                request_id=turn.request_id,
                task_id=turn.task_id,
                source_message_id=turn.trigger_message_id,
                source_thread_root_id=turn.thread_root_id,
                outcome="stale",
                details={
                    "reason": workspace_reason or "workspace_authorization_revoked"
                },
            )
        return False
    assert attempt is not None
    receipt_valid, receipt = await _native_terminal_receipt(
        db, turn=turn, attempt=attempt, frame=frame,
    )
    if not receipt_valid:
        await _audit_stale(db, turn=turn, agent_id=agent_id,
            reason="native_terminal_receipt_mismatch", attempt_number=attempt_number,
            generation=generation)
        return False
    if legacy:
        turn.protocol_version = 0
    now = _now()
    if workspace_attachment is not None and frame.event in {
        "handler_started",
        "handler_finished",
    }:
        from anygarden.workspaces.service import append_audit

        await append_audit(
            db,
            attachment=workspace_attachment,
            event_type=(
                "invocation_started"
                if frame.event == "handler_started"
                else "invocation_finished"
            ),
            request_id=turn.request_id,
            task_id=turn.task_id,
            source_message_id=turn.trigger_message_id,
            source_thread_root_id=turn.thread_root_id,
            outcome=getattr(frame, "outcome", None),
            details={"attempt": attempt.attempt_number},
        )
    if frame.event == "handler_started":
        if attempt.state in {"pending", "leased"}:
            attempt.state = "started"
            attempt.started_at = now
        if turn.state in {"pending", "retrying"}:
            turn.state = "leased"
    elif frame.event == "handler_finished" and frame.outcome not in {
        "queued",
        "retrying",
    }:
        if (turn.execution_id is not None and frame.outcome in {
            "failed", "timeout", "retry_exhausted", "rejected",
        } and turn.state in {"pending", "leased", "retrying"}):
            from anygarden.project_executions.recovery import on_turn_recovery

            if receipt is None:
                receipt_event = await db.scalar(select(ProjectExecutionEvent).where(
                    ProjectExecutionEvent.event_key ==
                    f"turn:{turn.request_id}:attempt:{attempt.attempt_number}:native-terminal",
                ))
                receipt = receipt_event.details if receipt_event is not None else None
            reason_code = (receipt or {}).get("reason_code") or "PROCESS_OUTCOME_UNKNOWN"
            safe = bool(receipt and receipt.get("retryable"))
            attempt.outcome = frame.outcome
            attempt.reason = reason_code
            retry_possible = bool(safe and receipt.get("transient")
                                  and turn.retry_count < turn.max_retries)
            admission = await _execution_admission(db, turn) if retry_possible else None
            if retry_possible and (admission.allowed or admission.wait):
                # Only the durable server retry owns another engine attempt.
                # Recovery retains this task/request and the prior receipt.
                attempt.lease_expires_at = now + timedelta(seconds=5) if admission.wait else now
                await on_turn_recovery(db, turn=turn, attempt=attempt,
                    phase="waiting_retry", reason_code=admission.reason_code if admission.wait else reason_code,
                    next_retry_at=now + timedelta(seconds=min(30, 5 * 2 ** turn.retry_count)))
            else:
                recovery_reason = (admission.reason_code if admission is not None else None) or reason_code
                await mark_turn_terminal(db, turn, state="failed", reason=recovery_reason,
                    at=now, attempt=attempt, attempt_state="failed",
                    attempt_outcome=frame.outcome, attempt_reason=reason_code)
                await on_turn_recovery(db, turn=turn, attempt=attempt,
                    phase="exhausted" if safe and receipt.get("transient") and not retry_possible else "action_required",
                    reason_code=recovery_reason)
        elif turn.state == "completed":
            attempt.state = "completed"
            attempt.ended_at = now
            attempt.outcome = frame.outcome
        elif frame.outcome == "cancelled":
            await mark_turn_terminal(
                db,
                turn,
                state="cancelled",
                reason="agent_cancelled",
                at=now,
                attempt=attempt,
                attempt_outcome="cancelled",
            )
        elif frame.outcome == "skipped":
            # #720 — the agent's policy declined this delivery (SKIP /
            # INGEST_ONLY). That is a deliberate, complete answer: close the
            # turn now instead of waiting out the lease, and never treat it
            # as a lost reply that deserves a retry. A turn already in its
            # completion CAS ("completing") is left to that path.
            if attempt.state in ACTIVE_ATTEMPT_STATES and turn.state in {
                "pending", "leased", "retrying",
            }:
                await mark_turn_terminal(
                    db,
                    turn,
                    state="completed",
                    reason="agent_skipped",
                    at=now,
                    attempt=attempt,
                    attempt_outcome="skipped",
                )
        elif attempt.state in ACTIVE_ATTEMPT_STATES:
            # A terminal lifecycle frame normally follows the agent's visible
            # reply, whose completion CAS has already closed the Turn. If that
            # reply never reached us, do not strand the active lease until its
            # original (potentially long) timeout: expire it now so the same
            # bounded recovery path fences the attempt and retries at most
            # once. Legacy attempts are still closed without redispatch.
            attempt.outcome = frame.outcome
            attempt.reason = f"agent_{frame.outcome}_without_completion"
            attempt.lease_expires_at = now
        if turn.execution_id is not None and turn.task_id is not None:
            # The native call can finish successfully while its domain task
            # remains blocked. Publish the final scoped attempt projection
            # after commit so clients retire their running recovery state.
            db.info.setdefault("project_execution_recovery_tasks", set()).add(
                turn.task_id
            )
    return True


@dataclass(slots=True)
class RecoveryResult:
    redispatched: int = 0
    cancelled: int = 0
    failed: int = 0
    drain_agents: set[str] | None = None
    recovery_task_ids: set[str] | None = None

    def __post_init__(self) -> None:
        if self.drain_agents is None:
            self.drain_agents = set()
        if self.recovery_task_ids is None:
            self.recovery_task_ids = set()


async def recover_stalled_turns(
    session_factory: Any,
    manager: Any,
    *,
    now: datetime | None = None,
    attempt_ids: set[str] | None = None,
    app: Any = None,
) -> RecoveryResult:
    """Fence expired/dead-generation attempts and redispatch at most once.

    ``attempt_ids`` scopes recovery to leases fenced by a confirmed shutdown;
    the periodic recovery worker leaves it unset to scan all agents.
    """

    current = now or _now()
    result = RecoveryResult()
    async with session_factory() as db:
        query = (
            select(AgentTurnAttempt.id)
            .join(Agent, Agent.id == AgentTurnAttempt.agent_id)
            .where(
                AgentTurnAttempt.state.in_(ACTIVE_ATTEMPT_STATES),
                or_(
                    and_(
                        AgentTurnAttempt.lease_expires_at.isnot(None),
                        AgentTurnAttempt.lease_expires_at <= current,
                    ),
                    Agent.actual_state == "crashed",
                    and_(
                        Agent.restart_deadline_at.isnot(None),
                        Agent.restart_deadline_at <= current,
                        AgentTurnAttempt.generation == Agent.generation,
                    ),
                ),
            )
        )
        if attempt_ids is not None:
            query = query.where(AgentTurnAttempt.id.in_(attempt_ids))
        ids = list((await db.scalars(query)).all())

    notices: list[Any] = []
    for attempt_id in ids:
        async with session_factory() as db:
            await begin_write_transaction(db)
            attempt = await db.get(AgentTurnAttempt, attempt_id)
            if attempt is None or attempt.state not in ACTIVE_ATTEMPT_STATES:
                continue
            turn_id = attempt.turn_id
            turn, changed_binding = await _lock_scoped_turn(db, turn_id)
            if changed_binding or turn is None:
                await db.rollback()
                continue
            attempt = await db.scalar(select(AgentTurnAttempt).where(
                AgentTurnAttempt.id == attempt_id,
                AgentTurnAttempt.turn_id == turn.request_id,
            ).execution_options(populate_existing=True).with_for_update())
            if (attempt is None or attempt.turn_id != turn.request_id
                or attempt.state not in ACTIVE_ATTEMPT_STATES):
                await db.rollback()
                continue
            agent = await db.get(Agent, attempt.agent_id) if attempt.agent_id else None
            if turn is None or agent is None or turn.state not in OPEN_TURN_STATES:
                continue
            reason = attempt.reason or "lease_expired"
            if agent.actual_state == "crashed":
                reason = "process_lost"
            elif (
                agent.restart_deadline_at is not None
                and agent.restart_deadline_at <= current
                and attempt.generation == int(agent.generation or 0)
            ):
                reason = "generation_interrupted"
            if turn.execution_id is not None:
                from anygarden.project_executions.recovery import on_turn_recovery

                execution_ok, _, _ = await _execution_gate(
                    db, turn, lock=True, completion_attempt=attempt,
                )
                if execution_ok:
                    accepted = await db.scalar(select(TaskResult.id).join(
                        Task, Task.id == TaskResult.task_id,
                    ).where(
                        Task.id == turn.task_id, Task.status == "done",
                        TaskResult.attempt_id == attempt.id,
                    ).limit(1))
                    if accepted is not None:
                        await mark_turn_terminal(db, turn, state="completed",
                            reason="accepted_result_recovered", at=current,
                            attempt=attempt, attempt_state="completed")
                        await db.commit()
                        continue
                    receipt_event = await db.scalar(select(ProjectExecutionEvent).where(
                        ProjectExecutionEvent.event_key ==
                        f"turn:{turn.request_id}:attempt:{attempt.attempt_number}:native-terminal",
                    ))
                    receipt = receipt_event.details if receipt_event is not None else None
                    safe = bool(receipt and receipt.get("retryable")
                                and receipt.get("local_execution_id") == attempt.local_execution_id)
                    # A lost process/lease is not proof that native effects
                    # stopped. Even a known permanent error needs a user to
                    # repair its cause; only a safe transient receipt retries.
                    if attempt.local_execution_id is not None and not (
                        safe and receipt.get("transient")
                    ):
                        code = (receipt or {}).get("reason_code") or "PROCESS_OUTCOME_UNKNOWN"
                        await mark_turn_terminal(db, turn, state="failed", reason=code,
                            at=current, attempt=attempt, attempt_state="failed",
                            attempt_outcome=attempt.outcome or "unknown", attempt_reason=code)
                        await on_turn_recovery(db, turn=turn, attempt=attempt,
                            phase="action_required", reason_code=code)
                        result.recovery_task_ids.update(db.info.pop(
                            "project_execution_recovery_tasks", set(),
                        ))
                        recovery_messages = list(db.info.pop("project_execution_messages", []))
                        usage_denials = dict(db.info.pop("project_execution_usage_denials", {}))
                        result.failed += 1
                        await db.commit()
                        if recovery_messages and app is not None and manager is not None:
                            from anygarden.mcp.project_tools import (
                                broadcast_project_messages,
                            )

                            await broadcast_project_messages(
                                db, app=app, messages=recovery_messages,
                            )
                        if usage_denials and app is not None:
                            from anygarden.project_executions.limits import (
                                apply_queued_usage_denials,
                            )

                            await apply_queued_usage_denials(app, denials=usage_denials)
                        continue
            fenced = await db.execute(
                update(AgentTurnAttempt)
                .where(
                    AgentTurnAttempt.id == attempt.id,
                    AgentTurnAttempt.state.in_(ACTIVE_ATTEMPT_STATES),
                )
                .values(
                    state="interrupted",
                    ended_at=current,
                    outcome=attempt.outcome if attempt.outcome in {
                        "failed", "timeout", "retry_exhausted",
                    } else "interrupted",
                    reason=reason,
                )
            )
            if fenced.rowcount != 1:
                await db.rollback()
                continue
            room = await db.get(Room, turn.room_id)
            participant = (
                await db.get(Participant, turn.target_participant_id)
                if turn.target_participant_id
                else None
            )
            gate_ok = (
                room is not None
                and room.archived_at is None
                and participant is not None
                and participant.room_id == turn.room_id
                and participant.agent_id == turn.agent_id
                and participant.role in AGENT_EXECUTION_ROLES
                and agent.desired_state == "running"
            )
            (
                workspace_ok,
                workspace_reason,
                workspace_attachment,
            ) = await _workspace_gate(db, turn)
            execution_ok, execution_reason, _ = await _execution_gate(
                db, turn, lock=True, completion_attempt=attempt
            )
            gate_ok = gate_ok and workspace_ok and execution_ok
            execution_completed = turn.execution_id is not None and await db.scalar(
                select(ProjectExecution.id).where(
                    ProjectExecution.id == turn.execution_id,
                    ProjectExecution.input_revision == turn.execution_input_revision,
                    ProjectExecution.status == "completed",
                )
            ) is not None
            retry_admission = None
            if (gate_ok and not execution_completed and turn.protocol_version != 0
                and turn.retry_count < turn.max_retries and turn.execution_id is not None):
                retry_admission = await _execution_admission(db, turn)
                if not retry_admission.allowed and retry_admission.wait:
                    # Undo only this proposed recovery fence. Budget waiting
                    # creates no attempt/outbox and consumes no retry count.
                    await db.rollback()
                    continue
            # The attempt was already fenced to "interrupted" above, so only
            # the turn itself transitions here.
            if not gate_ok:
                await mark_turn_terminal(
                    db,
                    turn,
                    state="cancelled",
                    reason=workspace_reason or execution_reason or "authorization_revoked",
                    at=current,
                )
                result.cancelled += 1
                event = "turn_cancelled"
            elif execution_completed:
                # The authoritative final report and exact accepted producer
                # result already exist. Recover the missing acknowledgement
                # without spawning new work for a completed execution.
                await mark_turn_terminal(db, turn, state="completed",
                                         reason="accepted_result_recovered", at=current)
                event = "turn_completion_recovered"
            elif turn.protocol_version == 0:
                await mark_turn_terminal(
                    db, turn, state="failed", reason="legacy_interrupted", at=current
                )
                result.failed += 1
                event = "turn_retry_exhausted"
            elif turn.retry_count >= turn.max_retries:
                await mark_turn_terminal(
                    db, turn, state="failed", reason="retry_exhausted", at=current
                )
                result.failed += 1
                event = "turn_retry_exhausted"
                if turn.execution_id is not None:
                    from anygarden.project_executions.recovery import on_turn_recovery

                    await on_turn_recovery(db, turn=turn, attempt=attempt,
                        phase="exhausted", reason_code=attempt.reason or "MODEL_EXECUTION_FAILED")
            elif retry_admission is not None and not retry_admission.allowed:
                code = retry_admission.reason_code or "EXECUTION_ADMISSION_DENIED"
                await mark_turn_terminal(db, turn, state="failed", reason=code, at=current)
                result.failed += 1
                event = "turn_retry_denied"
                from anygarden.project_executions.recovery import on_turn_recovery

                await on_turn_recovery(db, turn=turn, attempt=attempt,
                    phase="action_required", reason_code=code)
            else:
                next_number = turn.active_attempt + 1
                next_generation = int(
                    agent.pending_generation
                    if reason == "generation_interrupted"
                    and agent.pending_generation is not None
                    else agent.generation or 0
                )
                next_attempt = AgentTurnAttempt(
                    id=str(uuid4()),
                    turn_id=turn.request_id,
                    agent_id=turn.agent_id,
                    attempt_number=next_number,
                    generation=next_generation,
                    lease_token=_lease_token(),
                    state="pending",
                )
                db.add(next_attempt)
                db.add(
                    AgentTurnOutbox(
                        id=str(uuid4()),
                        turn_id=turn.request_id,
                        attempt_id=next_attempt.id,
                        room_id=turn.room_id,
                        participant_id=turn.target_participant_id,
                        state="pending",
                        available_at=(current + timedelta(seconds=min(30, 5 * 2 ** turn.retry_count))
                                      if turn.execution_id is not None else current),
                    )
                )
                turn.state = "retrying"
                turn.active_attempt = next_number
                turn.retry_count += 1
                turn.terminal_reason = None
                result.redispatched += 1
                event = "turn_redispatched"
                if turn.execution_id is not None:
                    from anygarden.project_executions.recovery import on_turn_recovery

                    await on_turn_recovery(db, turn=turn, attempt=next_attempt,
                        phase="retrying", reason_code=reason,
                        next_retry_at=current + timedelta(seconds=min(30, 5 * 2 ** (turn.retry_count - 1))))
            turn.updated_at = current
            db.add(
                ActivityLog(
                    agent_id=turn.agent_id,
                    event_type="turn_interrupted",
                    request_id=turn.request_id,
                    room_id=turn.room_id,
                    details={
                        "reason": reason,
                        "attempt": attempt.attempt_number,
                        "generation": attempt.generation,
                    },
                )
            )
            if workspace_attachment is not None and not workspace_ok:
                from anygarden.workspaces.service import append_audit

                await append_audit(
                    db,
                    attachment=workspace_attachment,
                    event_type="retry_denied",
                    request_id=turn.request_id,
                    task_id=turn.task_id,
                    source_message_id=turn.trigger_message_id,
                    source_thread_root_id=turn.thread_root_id,
                    outcome="cancelled",
                    details={"reason": turn.terminal_reason},
                )
            db.add(
                ActivityLog(
                    agent_id=turn.agent_id,
                    event_type=event,
                    request_id=turn.request_id,
                    room_id=turn.room_id,
                    details={
                        "reason": turn.terminal_reason or reason,
                        "attempt": turn.active_attempt,
                        "generation": (
                            agent.pending_generation
                            if reason == "generation_interrupted"
                            else agent.generation
                        ),
                    },
                )
            )
            if reason == "generation_interrupted":
                result.drain_agents.add(agent.id)
            if turn.state == "failed" and room is not None and room.archived_at is None:
                notice = await append_message(
                    db,
                    turn.room_id,
                    None,
                    FAILURE_NOTICE,
                    {
                        "system_origin": "turn_retry_exhausted",
                        "request_id": turn.request_id,
                    },
                    thread_root_id=turn.thread_root_id,
                )
                notices.append(message_to_frame(notice))
            result.recovery_task_ids.update(db.info.pop(
                "project_execution_recovery_tasks", set(),
            ))
            recovery_messages = list(db.info.pop("project_execution_messages", []))
            usage_denials = dict(db.info.pop("project_execution_usage_denials", {}))
            await db.commit()
            if recovery_messages and app is not None and manager is not None:
                from anygarden.mcp.project_tools import broadcast_project_messages

                await broadcast_project_messages(db, app=app, messages=recovery_messages)
            if usage_denials and app is not None:
                from anygarden.project_executions.limits import (
                    apply_queued_usage_denials,
                )

                await apply_queued_usage_denials(app, denials=usage_denials)

    for frame in notices:
        if manager is not None:
            await manager.broadcast(frame.room_id, frame)
    if result.recovery_task_ids:
        from anygarden.project_executions.recovery import fanout_recovery_updates

        await fanout_recovery_updates(session_factory, manager, result.recovery_task_ids)
    return result


async def cancel_invalid_turns(session_factory: Any) -> int:
    """Cancel open turns whose stop/archive/membership gate was revoked."""

    async with session_factory() as db:
        await begin_write_transaction(db)
        turn_ids = list(
            (
                await db.scalars(
                    select(AgentTurn.request_id).where(AgentTurn.state.in_(OPEN_TURN_STATES))
                    .order_by(AgentTurn.execution_id.asc().nulls_last(), AgentTurn.request_id)
                )
            ).all()
        )
        cancelled = 0
        for request_id in turn_ids:
            turn, changed_binding = await _lock_scoped_turn(db, request_id)
            if changed_binding or turn is None:
                continue
            gate = (
                await db.execute(
                    select(Participant.id)
                    .join(Room, Room.id == Participant.room_id)
                    .join(Agent, Agent.id == Participant.agent_id)
                    .where(
                        Participant.id == turn.target_participant_id,
                        Participant.room_id == turn.room_id,
                        Participant.agent_id == turn.agent_id,
                        Participant.role.in_(AGENT_EXECUTION_ROLES),
                        Room.archived_at.is_(None),
                        Agent.desired_state == "running",
                    )
                )
            ).scalar_one_or_none()
            (
                workspace_ok,
                workspace_reason,
                workspace_attachment,
            ) = await _workspace_gate(db, turn)
            attempt = (
                await db.execute(
                    select(AgentTurnAttempt).where(
                        AgentTurnAttempt.turn_id == turn.request_id,
                        AgentTurnAttempt.attempt_number == turn.active_attempt,
                    )
                )
            ).scalar_one_or_none()
            execution_ok, execution_reason, _ = await _execution_gate(
                db, turn, lock=True, completion_attempt=attempt
            )
            if gate is not None and workspace_ok and execution_ok:
                continue
            now = _now()
            reason = workspace_reason or execution_reason or "authorization_revoked"
            # An attempt that already closed keeps its own terminal fields.
            if attempt is not None and attempt.state in {"completed", "cancelled"}:
                attempt = None
            await mark_turn_terminal(
                db,
                turn,
                state="cancelled",
                reason=reason,
                at=now,
                attempt=attempt,
                attempt_reason=reason,
            )
            await db.execute(
                update(AgentTurnOutbox)
                .where(
                    AgentTurnOutbox.turn_id == turn.request_id,
                    AgentTurnOutbox.state == "pending",
                )
                .values(state="cancelled", last_error=turn.terminal_reason)
            )
            db.add(
                ActivityLog(
                    agent_id=turn.agent_id,
                    event_type="turn_cancelled",
                    request_id=turn.request_id,
                    room_id=turn.room_id,
                    details={
                        "reason": turn.terminal_reason,
                        "attempt": turn.active_attempt,
                    },
                )
            )
            if workspace_attachment is not None and not workspace_ok:
                from anygarden.workspaces.service import append_audit

                await append_audit(
                    db,
                    attachment=workspace_attachment,
                    event_type="turn_cancelled",
                    request_id=turn.request_id,
                    task_id=turn.task_id,
                    source_message_id=turn.trigger_message_id,
                    source_thread_root_id=turn.thread_root_id,
                    outcome="cancelled",
                    details={"reason": turn.terminal_reason},
                )
            cancelled += 1
        if cancelled:
            await db.commit()
        return cancelled
