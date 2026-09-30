"""Structured peer asks through the ``ask_peer`` MCP tool (#737, #762).

Before this tool the only way for an agent to call a peer was to write a
``<@user:PARTICIPANT_ID>`` routing token into its reply. Models often
forgot the token ("I'll ask PM" with nobody called), and a call to a peer
that was already answering could only be stripped after the fact (#743).

This module decides whether a caller may ask a target *during* its turn.
A rejection carries the messages posted since the turn started, so the
model can answer from them in the same turn. Accepted questions are posted
at once and their answers collected by
:mod:`anygarden.orchestration.peer_fanin` (#762).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import AgentTurn, AgentTurnAttempt, Message, Participant
from anygarden.rooms.roster import build_participants_brief
from anygarden.turns.service import OPEN_TURN_STATES

logger = structlog.get_logger(__name__)

MAX_QUESTION_CHARS = 2000
MAX_ASKS_PER_CALL = 8
MAX_SINCE_MESSAGES = 10
MAX_SINCE_CHARS = 400

# Same vocabulary as ``metadata.peer_call_undelivered`` (#743).
REASON_ALREADY_ANSWERING = "already_answering"
REASON_LIMIT_REACHED = "limit_reached"


@dataclass
class AskCaller:
    """The asking agent's side of an ``ask_peer`` call."""

    participant: Participant
    turn: AgentTurn | None
    hop: int
    names: dict[str, str]


@dataclass
class PeerAskDecision:
    """Outcome of :func:`check_peer_ask` for one target.

    ``status`` is ``accepted`` (the question may be posted), ``rejected``
    (a normal outcome the model should act on) or ``invalid`` (bad
    arguments: unknown participant, a person, or the caller itself).
    """

    status: str
    reason: str | None = None
    detail: str | None = None
    target: Participant | None = None
    target_name: str | None = None
    since_turn_start: list[dict[str, Any]] = field(default_factory=list)


async def turn_hop(db: AsyncSession, turn: AgentTurn | None) -> int:
    """How deep a peer call made from *turn* goes (#756).

    A call from a turn that a peer's call started is hop 2; any other
    turn (a person's message, a delegation, a round-robin or handoff
    nomination, a fan-in wake, or no known turn) calls at hop 1.
    """
    if turn is None or turn.trigger_message_id is None:
        return 1
    trigger = await db.get(Message, turn.trigger_message_id)
    if trigger is None or trigger.participant_id is None:
        return 1
    author = await db.get(Participant, trigger.participant_id)
    if author is None or author.agent_id is None:
        return 1
    meta = trigger.extra_metadata or {}
    if "delegation_target_participant_id" in meta or meta.get("delegation_id"):
        return 1
    called = {
        str(m.get("id"))
        for m in meta.get("mentions") or []
        if isinstance(m, dict) and m.get("type") == "user"
    }
    return 2 if turn.target_participant_id in called else 1


async def sender_hop(
    db: AsyncSession, *, request_id: str | None, participant_id: str
) -> int:
    """Hop of a reply that echoes *request_id*, trusting only the sender's turn."""
    if not request_id:
        return 1
    turn = await db.get(AgentTurn, request_id)
    if turn is None or turn.target_participant_id != participant_id:
        return 1
    return await turn_hop(db, turn)


async def resolve_caller(
    db: AsyncSession, *, agent_id: str, room_id: str
) -> AskCaller | None:
    """The caller's participant, open turn and hop; ``None`` if not in the room."""
    caller = (
        await db.execute(
            select(Participant).where(
                Participant.room_id == room_id,
                Participant.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()
    if caller is None:
        return None
    turn = await _open_turn(db, room_id=room_id, agent_id=agent_id, caller_pid=caller.id)
    names = {
        b.id: b.display_name
        for b in await build_participants_brief(db, room_id=room_id)
    }
    return AskCaller(
        participant=caller, turn=turn, hop=await turn_hop(db, turn), names=names
    )


async def check_peer_ask(
    db: AsyncSession, *, caller: AskCaller, target_pid: str
) -> PeerAskDecision:
    """Decide whether *caller* may ask *target_pid* now (budget aside).

    ``already_answering`` means the target has an open turn in this room:
    it is answering something here and would only see the question later.
    A peer that already finished may be asked again (#762).
    """
    room_id = caller.participant.room_id
    target = await db.get(Participant, target_pid)
    if target is None or target.room_id != room_id:
        return PeerAskDecision(
            "invalid", detail=f"{target_pid} is not a participant of this room."
        )
    if target.id == caller.participant.id:
        return PeerAskDecision("invalid", detail="You cannot ask yourself.")
    if target.agent_id is None:
        return PeerAskDecision(
            "invalid",
            detail=(
                "ask_peer only calls agents. Address people by name in "
                "your reply; everyone in the room sees it."
            ),
        )
    decision = PeerAskDecision(
        "accepted", target=target, target_name=caller.names.get(target.id)
    )
    answering = await db.scalar(
        select(AgentTurn.request_id)
        .where(
            AgentTurn.room_id == room_id,
            AgentTurn.target_participant_id == target.id,
            AgentTurn.state.in_(OPEN_TURN_STATES),
        )
        .limit(1)
    )
    if answering is not None:
        decision.status = "rejected"
        decision.reason = REASON_ALREADY_ANSWERING
    return decision


async def since_turn_start(db: AsyncSession, caller: AskCaller) -> list[dict[str, Any]]:
    """Messages posted in the caller's conversation after its turn began."""
    turn = caller.turn
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
            "speaker": caller.names.get(m.participant_id or "", "unknown"),
            "content": content,
        })
    return out


async def _open_turn(
    db: AsyncSession, *, room_id: str, agent_id: str, caller_pid: str
) -> AgentTurn | None:
    """The caller's running turn: the MCP call carries no turn id.

    Prefer the newest open turn whose active attempt is leased or started
    (the one executing now); fall back to the newest open turn.
    """
    rows = (
        await db.execute(
            select(AgentTurn, AgentTurnAttempt.state)
            .outerjoin(
                AgentTurnAttempt,
                (AgentTurnAttempt.turn_id == AgentTurn.request_id)
                & (AgentTurnAttempt.attempt_number == AgentTurn.active_attempt),
            )
            .where(
                AgentTurn.room_id == room_id,
                AgentTurn.agent_id == agent_id,
                AgentTurn.target_participant_id == caller_pid,
                AgentTurn.state.in_(OPEN_TURN_STATES),
            )
            .order_by(AgentTurn.created_at.desc())
        )
    ).all()
    for turn, attempt_state in rows:
        if attempt_state in {"leased", "started"}:
            return turn
    return rows[0][0] if rows else None
