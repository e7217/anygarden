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

from anygarden.db.models import (
    ActivityLog,
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    AgentTurnOutbox,
    Message,
    Participant,
    Room,
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

    agent = await db.get(Agent, agent_id)
    generation = int(agent.generation or 0) if agent is not None else 0
    state = "pending"
    reason: str | None = None
    if agent is None or agent.desired_state != "running":
        state = "cancelled"
        reason = "agent_not_running"

    turn = AgentTurn(
        request_id=rid,
        room_id=room_id,
        target_participant_id=participant_id,
        agent_id=agent_id,
        trigger_message_id=trigger_message_id,
        thread_root_id=thread_root_id,
        task_id=task_id,
        idempotency_key=key,
        state=state,
        active_attempt=1,
        retry_count=retry_count,
        max_retries=max_retries,
        terminal_reason=reason,
    )
    db.add(turn)
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


def _durable_metadata(
    turn: AgentTurn, attempt: AgentTurnAttempt, base: dict[str, Any] | None
) -> dict[str, Any]:
    metadata = dict(base or {})
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
    return metadata


async def deliver_pending_outbox(
    session_factory: Any,
    manager: Any,
    *,
    participant_ids: Iterable[str] | None = None,
    limit: int = 100,
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
                turn.state = "cancelled"
                turn.terminal_reason = "authorization_revoked"
                attempt.state = "cancelled"
                attempt.reason = "authorization_revoked"
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
                turn.state = "cancelled"
                turn.terminal_reason = "trigger_message_deleted"
                attempt.state = "cancelled"
                attempt.reason = "trigger_message_deleted"
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
            subscription_generation = await manager.participant_generation(
                row.participant_id
            )
            legacy = subscription_generation is None
            if not legacy and subscription_generation != attempt.generation:
                continue
            metadata = dict(msg.extra_metadata or {})
            if legacy:
                # Mixed rollout: deliver once using the pre-Phase-4 contract,
                # accept at most one legacy completion, and never auto-retry it.
                metadata["request_id"] = turn.request_id
                turn.protocol_version = 0
            else:
                metadata = _durable_metadata(turn, attempt, metadata)
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
            row2 = await db.get(AgentTurnOutbox, outbox_id)
            turn2 = await db.get(AgentTurn, turn_id)
            attempt2 = await db.get(AgentTurnAttempt, attempt_id)
            if row2 is None or turn2 is None or attempt2 is None:
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
    outcome: Literal["legacy", "accept", "idempotent", "stale"]
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
) -> CompletionDecision:
    """Reserve the one user-visible completion before appending its message."""

    if not request_id:
        return CompletionDecision("legacy")
    turn = await db.get(AgentTurn, request_id)
    if turn is None:
        return CompletionDecision("legacy")
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


async def finish_completion(
    db: AsyncSession,
    *,
    turn: AgentTurn,
    attempt: AgentTurnAttempt,
    message_id: str,
) -> None:
    now = _now()
    turn.state = "completed"
    turn.accepted_message_id = message_id
    turn.completed_at = now
    turn.updated_at = now
    attempt.state = "completed"
    attempt.ended_at = now
    attempt.outcome = "ok"
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


async def record_lifecycle(
    db: AsyncSession,
    *,
    agent_id: str,
    frame: Any,
) -> bool:
    """Validate and apply a lifecycle frame; return False when fenced."""

    turn = await db.get(AgentTurn, frame.request_id)
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
    valid = (
        attempt is not None
        and gate is not None
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
                "lifecycle_authorization_revoked"
                if gate is None
                else "lifecycle_lease_mismatch"
            ),
            attempt_number=attempt_number,
            generation=generation,
        )
        return False
    assert attempt is not None
    if legacy:
        turn.protocol_version = 0
    now = _now()
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
        if turn.state == "completed":
            attempt.state = "completed"
            attempt.ended_at = now
            attempt.outcome = frame.outcome
        elif frame.outcome == "cancelled":
            attempt.state = "cancelled"
            attempt.ended_at = now
            attempt.outcome = "cancelled"
            turn.state = "cancelled"
            turn.terminal_reason = "agent_cancelled"
            turn.completed_at = now
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
    return True


@dataclass(slots=True)
class RecoveryResult:
    redispatched: int = 0
    cancelled: int = 0
    failed: int = 0
    drain_agents: set[str] | None = None

    def __post_init__(self) -> None:
        if self.drain_agents is None:
            self.drain_agents = set()


async def recover_stalled_turns(
    session_factory: Any,
    manager: Any,
    *,
    now: datetime | None = None,
) -> RecoveryResult:
    """Fence expired/dead-generation attempts and redispatch at most once."""

    current = now or _now()
    result = RecoveryResult()
    async with session_factory() as db:
        ids = list(
            (
                await db.scalars(
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
            ).all()
        )

    notices: list[Any] = []
    for attempt_id in ids:
        async with session_factory() as db:
            attempt = await db.get(AgentTurnAttempt, attempt_id)
            if attempt is None or attempt.state not in ACTIVE_ATTEMPT_STATES:
                continue
            turn = await db.get(AgentTurn, attempt.turn_id)
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
            fenced = await db.execute(
                update(AgentTurnAttempt)
                .where(
                    AgentTurnAttempt.id == attempt.id,
                    AgentTurnAttempt.state.in_(ACTIVE_ATTEMPT_STATES),
                )
                .values(
                    state="interrupted",
                    ended_at=current,
                    outcome="interrupted",
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
            if not gate_ok:
                turn.state = "cancelled"
                turn.terminal_reason = "authorization_revoked"
                turn.completed_at = current
                result.cancelled += 1
                event = "turn_cancelled"
            elif turn.protocol_version == 0:
                turn.state = "failed"
                turn.terminal_reason = "legacy_interrupted"
                turn.completed_at = current
                result.failed += 1
                event = "turn_retry_exhausted"
            elif turn.retry_count >= turn.max_retries:
                turn.state = "failed"
                turn.terminal_reason = "retry_exhausted"
                turn.completed_at = current
                result.failed += 1
                event = "turn_retry_exhausted"
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
                    )
                )
                turn.state = "retrying"
                turn.active_attempt = next_number
                turn.retry_count += 1
                turn.terminal_reason = None
                result.redispatched += 1
                event = "turn_redispatched"
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
            await db.commit()

    for frame in notices:
        if manager is not None:
            await manager.broadcast(frame.room_id, frame)
    return result


async def cancel_invalid_turns(session_factory: Any) -> int:
    """Cancel open turns whose stop/archive/membership gate was revoked."""

    async with session_factory() as db:
        turns = list(
            (
                await db.scalars(
                    select(AgentTurn).where(AgentTurn.state.in_(OPEN_TURN_STATES))
                )
            ).all()
        )
        cancelled = 0
        for turn in turns:
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
            if gate is not None:
                continue
            now = _now()
            turn.state = "cancelled"
            turn.terminal_reason = "authorization_revoked"
            turn.completed_at = now
            attempt = (
                await db.execute(
                    select(AgentTurnAttempt).where(
                        AgentTurnAttempt.turn_id == turn.request_id,
                        AgentTurnAttempt.attempt_number == turn.active_attempt,
                    )
                )
            ).scalar_one_or_none()
            if attempt is not None and attempt.state not in {"completed", "cancelled"}:
                attempt.state = "cancelled"
                attempt.ended_at = now
                attempt.reason = "authorization_revoked"
            await db.execute(
                update(AgentTurnOutbox)
                .where(
                    AgentTurnOutbox.turn_id == turn.request_id,
                    AgentTurnOutbox.state == "pending",
                )
                .values(state="cancelled", last_error="authorization_revoked")
            )
            db.add(
                ActivityLog(
                    agent_id=turn.agent_id,
                    event_type="turn_cancelled",
                    request_id=turn.request_id,
                    room_id=turn.room_id,
                    details={
                        "reason": "authorization_revoked",
                        "attempt": turn.active_attempt,
                    },
                )
            )
            cancelled += 1
        if cancelled:
            await db.commit()
        return cancelled
