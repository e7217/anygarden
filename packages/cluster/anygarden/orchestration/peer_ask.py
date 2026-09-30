"""Structured peer asks through the ``ask_peer`` MCP tool (#737).

Before this tool the only way for an agent to call a peer was to write a
``<@user:PARTICIPANT_ID>`` routing token into its reply. Models often
forgot the token ("I'll ask PM" with nobody called), and a call to a peer
that was already answering could only be stripped after the fact (#743).

The tool splits the call from the prose:

1. ``ask_peer`` checks the target *during* the caller's turn and either
   rejects it (with the messages posted since the turn started, so the
   model can fix its reply in the same turn) or schedules it here.
2. When the caller's final reply is stored, the WS handler takes the
   scheduled asks and posts each one as a thread reply under that final
   reply, as the caller. That synthetic send runs through the normal
   agent-send path (peer safety net, thread-mention turn creation,
   ``peer_call_undelivered``), so no turn/lease logic is duplicated.

State is in memory, like :class:`PeerHandoffBudget`: exact in a single
process, and an ask lives for at most one turn anyway.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import AgentTurn, Message, Participant
from anygarden.orchestration.rules import PeerHandoffBudget
from anygarden.rooms.roster import build_participants_brief
from anygarden.turns.service import OPEN_TURN_STATES

logger = structlog.get_logger(__name__)

PEER_ASK_TTL_SECONDS = 600.0
MAX_QUESTION_CHARS = 2000
MAX_SINCE_MESSAGES = 10
MAX_SINCE_CHARS = 400

# Same vocabulary as ``metadata.peer_call_undelivered`` (#743).
REASON_ALREADY_ANSWERING = "already_answering"
REASON_LIMIT_REACHED = "limit_reached"


@dataclass(frozen=True)
class PeerAsk:
    target_pid: str
    question: str
    # The caller's open turn when the tool ran; ``None`` when no durable
    # turn was found (the ask then rides the caller's next reply).
    request_id: str | None
    created_at: float


class PendingPeerAsks:
    """Asks scheduled by ``ask_peer``, keyed by ``(agent_id, room_id)``."""

    def __init__(
        self,
        ttl_seconds: float = PEER_ASK_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._asks: dict[tuple[str, str], list[PeerAsk]] = {}

    def now(self) -> float:
        return self._clock()

    def schedule(self, agent_id: str, room_id: str, ask: PeerAsk) -> None:
        """Add *ask*; a newer ask to the same target replaces the older one."""
        key = (agent_id, room_id)
        kept = [a for a in self._asks.get(key, []) if a.target_pid != ask.target_pid]
        kept.append(ask)
        self._asks[key] = kept

    def take(
        self, agent_id: str, room_id: str, *, request_id: str | None
    ) -> list[PeerAsk]:
        """Remove and return the asks that belong to a reply.

        An ask bound to a turn is taken only by a reply carrying that
        turn's ``request_id``; an unbound ask is taken by any reply.
        Expired asks are dropped with a warning.
        """
        key = (agent_id, room_id)
        pending = self._drop_expired(key)
        taken = [a for a in pending if a.request_id is None or a.request_id == request_id]
        left = [a for a in pending if a not in taken]
        if left:
            self._asks[key] = left
        else:
            self._asks.pop(key, None)
        return taken

    def clear_room(self, room_id: str) -> list[PeerAsk]:
        """Forget every ask in *room_id* (a human opened a new turn)."""
        dropped: list[PeerAsk] = []
        for key in [k for k in self._asks if k[1] == room_id]:
            dropped.extend(self._asks.pop(key))
        if dropped:
            logger.warning(
                "peer_ask.dropped",
                room_id=room_id,
                reason="new_user_turn",
                target_participant_ids=[a.target_pid for a in dropped],
            )
        return dropped

    def pending(self, agent_id: str, room_id: str) -> list[PeerAsk]:
        return list(self._drop_expired((agent_id, room_id)))

    def _drop_expired(self, key: tuple[str, str]) -> list[PeerAsk]:
        asks = self._asks.get(key, [])
        cutoff = self._clock() - self._ttl
        fresh = [a for a in asks if a.created_at >= cutoff]
        if len(fresh) != len(asks):
            logger.warning(
                "peer_ask.dropped",
                agent_id=key[0],
                room_id=key[1],
                reason="expired",
                target_participant_ids=[a.target_pid for a in asks if a not in fresh],
            )
            if fresh:
                self._asks[key] = fresh
            else:
                self._asks.pop(key, None)
        return fresh


@dataclass
class PeerAskDecision:
    """Outcome of :func:`check_peer_ask`.

    ``status`` is ``scheduled`` (the caller may proceed), ``rejected``
    (a normal outcome the model should act on) or ``invalid`` (bad
    arguments: unknown participant, a person, or the caller itself).
    """

    status: str
    reason: str | None = None
    detail: str | None = None
    caller_pid: str | None = None
    target_name: str | None = None
    request_id: str | None = None
    since_turn_start: list[dict[str, Any]] = field(default_factory=list)


async def check_peer_ask(
    db: AsyncSession,
    *,
    budget: PeerHandoffBudget | None,
    agent_id: str,
    room_id: str,
    target_pid: str,
) -> PeerAskDecision:
    """Decide whether *agent_id* may ask *target_pid* in *room_id* now.

    Reads the peer budget without consuming it: the slot is spent when
    the scheduled ask passes the WS safety net, so a tool call whose
    reply never goes out costs nothing.
    """
    caller = (
        await db.execute(
            select(Participant).where(
                Participant.room_id == room_id,
                Participant.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()
    if caller is None:
        return PeerAskDecision("invalid", detail="You are not a participant of this room.")

    target = await db.get(Participant, target_pid)
    if target is None or target.room_id != room_id:
        return PeerAskDecision(
            "invalid",
            detail=f"{target_pid} is not a participant of this room.",
            caller_pid=caller.id,
        )
    if target.id == caller.id:
        return PeerAskDecision(
            "invalid", detail="You cannot ask yourself.", caller_pid=caller.id
        )
    if target.agent_id is None:
        return PeerAskDecision(
            "invalid",
            detail=(
                "ask_peer only calls agents. Address people by name in "
                "your reply; everyone in the room sees it."
            ),
            caller_pid=caller.id,
        )

    names = {b.id: b.display_name for b in await build_participants_brief(db, room_id=room_id)}
    turn = await _open_turn(db, room_id=room_id, agent_id=agent_id, caller_pid=caller.id)
    decision = PeerAskDecision(
        "scheduled",
        caller_pid=caller.id,
        target_name=names.get(target.id),
        request_id=turn.request_id if turn is not None else None,
    )

    if budget is not None:
        if target.id in budget.woken(room_id):
            decision.status = "rejected"
            decision.reason = REASON_ALREADY_ANSWERING
        elif budget.would_block(room_id):
            decision.status = "rejected"
            decision.reason = REASON_LIMIT_REACHED
    if decision.status == "rejected":
        decision.since_turn_start = await _since_turn_start(db, turn=turn, names=names)
    return decision


async def _open_turn(
    db: AsyncSession, *, room_id: str, agent_id: str, caller_pid: str
) -> AgentTurn | None:
    return (
        await db.execute(
            select(AgentTurn)
            .where(
                AgentTurn.room_id == room_id,
                AgentTurn.agent_id == agent_id,
                AgentTurn.target_participant_id == caller_pid,
                AgentTurn.state.in_(OPEN_TURN_STATES),
            )
            .order_by(AgentTurn.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _since_turn_start(
    db: AsyncSession, *, turn: AgentTurn | None, names: dict[str, str]
) -> list[dict[str, Any]]:
    """Messages posted in the caller's conversation after its turn began."""
    if turn is None or turn.trigger_message_id is None:
        return []
    trigger = await db.get(Message, turn.trigger_message_id)
    if trigger is None:
        return []
    stmt = select(Message).where(
        Message.room_id == turn.room_id,
        Message.seq > trigger.seq,
    )
    if turn.thread_root_id is None:
        stmt = stmt.where(Message.root_message_id.is_(None))
    else:
        stmt = stmt.where(Message.root_message_id == turn.thread_root_id)
    rows = (
        await db.execute(stmt.order_by(Message.seq.desc()).limit(MAX_SINCE_MESSAGES))
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for m in reversed(rows):
        content = m.content
        if len(content) > MAX_SINCE_CHARS:
            content = content[:MAX_SINCE_CHARS] + "…"
        out.append({
            "seq": m.seq,
            "speaker": names.get(m.participant_id or "", "unknown"),
            "content": content,
        })
    return out
