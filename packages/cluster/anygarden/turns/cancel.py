"""ENG-09 — user cancellation of an open general-room agent turn.

Execution-bound turns are fenced by ``project_executions.stop_service``
(exact stop receipts); this path only covers turns with no execution
binding, where the server's durable state is authoritative and the agent is
told best-effort to stop its in-flight engine call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    ActivityLog,
    AgentTurn,
    AgentTurnAttempt,
    AgentTurnOutbox,
    Task,
)
from anygarden.turns.service import (
    OPEN_TURN_STATES,
    _lock_scoped_turn,
    mark_turn_terminal,
)

USER_CANCELLED = "user_cancelled"
_CANCELLABLE_ATTEMPT_STATES = frozenset({"pending", "leased", "started"})


class TurnCancelError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TurnCancelResult:
    turn: AgentTurn
    changed: bool
    # Set when an attempt may already be running on the agent and should be
    # told to stop: (participant_id, attempt_number, generation).
    stop_target: tuple[str, int, int] | None = None


async def is_execution_bound(db: AsyncSession, turn: AgentTurn) -> bool:
    if turn.execution_id is not None:
        return True
    if turn.task_id is None:
        return False
    return (
        await db.scalar(select(Task.execution_id).where(Task.id == turn.task_id))
    ) is not None


async def load_turn(db: AsyncSession, request_id: str) -> AgentTurn:
    turn = await db.get(AgentTurn, request_id)
    if turn is None:
        raise TurnCancelError(404, "TURN_NOT_FOUND", "Turn not found")
    return turn


async def cancel_general_turn(
    db: AsyncSession, request_id: str, *, actor_user_id: str | None
) -> TurnCancelResult:
    """Make the turn terminal and fence its outbox/attempt. Never commits."""

    turn, changed_binding = await _lock_scoped_turn(db, request_id)
    if changed_binding:
        raise TurnCancelError(409, "TURN_BOUND_TO_EXECUTION",
                              "This turn belongs to a project execution; cancel the execution instead")
    if turn is None:
        raise TurnCancelError(404, "TURN_NOT_FOUND", "Turn not found")
    if await is_execution_bound(db, turn):
        raise TurnCancelError(409, "TURN_BOUND_TO_EXECUTION",
                              "This turn belongs to a project execution; cancel the execution instead")
    if turn.state == "cancelled":
        return TurnCancelResult(turn=turn, changed=False)
    if turn.state not in OPEN_TURN_STATES:
        raise TurnCancelError(409, "TURN_ALREADY_TERMINAL", f"Turn is already {turn.state}")
    if turn.state == "completing":
        # The single visible reply is already being committed (completion CAS).
        raise TurnCancelError(409, "TURN_COMPLETING", "The agent's reply is already being delivered")

    now = datetime.now(UTC)
    attempt = await db.scalar(
        select(AgentTurnAttempt).where(
            AgentTurnAttempt.turn_id == turn.request_id,
            AgentTurnAttempt.attempt_number == turn.active_attempt,
        ).with_for_update()
    )
    stop_target = None
    if attempt is not None and attempt.state in _CANCELLABLE_ATTEMPT_STATES:
        if attempt.state != "pending" and turn.target_participant_id:
            stop_target = (turn.target_participant_id, attempt.attempt_number, attempt.generation)
    else:
        attempt = None
    await mark_turn_terminal(
        db, turn, state="cancelled", reason=USER_CANCELLED, at=now,
        attempt=attempt, attempt_outcome="cancelled", attempt_reason=USER_CANCELLED,
    )
    await db.execute(
        update(AgentTurnOutbox)
        .where(
            AgentTurnOutbox.turn_id == turn.request_id,
            AgentTurnOutbox.state.in_({"pending", "delivering"}),
        )
        .values(state="cancelled", last_error=USER_CANCELLED)
    )
    db.add(ActivityLog(
        agent_id=turn.agent_id,
        event_type="turn_cancelled",
        request_id=turn.request_id,
        room_id=turn.room_id,
        details={"reason": USER_CANCELLED, "attempt": turn.active_attempt,
                 "actor_user_id": actor_user_id},
    ))
    await db.flush()
    return TurnCancelResult(turn=turn, changed=True, stop_target=stop_target)


async def notify_turn_cancelled(manager, turn: AgentTurn, stop_target) -> bool:
    """Best-effort after commit: stop the engine call and clear typing."""
    if manager is None:
        return False
    from anygarden.ws.protocol import TurnCancelOut, TypingOut

    sent = False
    if stop_target is not None and turn.agent_id:
        participant_id, attempt_number, generation = stop_target
        frame = TurnCancelOut(
            agent_id=turn.agent_id, room_id=turn.room_id, request_id=turn.request_id,
            attempt=attempt_number, generation=generation, reason=USER_CANCELLED,
        )
        try:
            sent = bool(await manager.send_to(participant_id, frame, expected_generation=generation))
        except Exception:  # noqa: BLE001 — durable cancel already committed
            sent = False
    if turn.target_participant_id:
        try:
            await manager.broadcast(turn.room_id, TypingOut(
                room_id=turn.room_id, participant_id=turn.target_participant_id, is_typing=False,
            ))
        except Exception:  # noqa: BLE001, S110 — indicator is cosmetic
            pass
    return sent
