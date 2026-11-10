"""Question chains as people see them (#1006).

One person's message can now spread through agent links: the agent they
asked asks a peer, which asks another, and so on. Every ``ask_peer`` group
of that spread shares a ``chain_id``. This module turns a chain into the
tree the thread panel draws (who asked whom, what is still running) and
stops a whole chain at a person's request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    AgentTurn,
    AgentTurnAttempt,
    Participant,
    PeerAskGroup,
    PeerAskTarget,
)
from anygarden.orchestration.peer_fanin import (
    TERMINAL_TARGET_STATES,
    _origin_message,
    _settle,
)
from anygarden.turns.service import OPEN_TURN_STATES

ACTIVE_GROUP_STATES = frozenset({"collecting", "ready", "waking"})
USER_CANCELLED = "user_cancelled"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _names(db: AsyncSession, pids: set[str]) -> dict[str, tuple[str | None, str]]:
    """participant id → (agent id, display name)."""
    if not pids:
        return {}
    from sqlalchemy.orm import selectinload

    rows = (
        await db.execute(
            select(Participant)
            .where(Participant.id.in_(pids))
            .options(selectinload(Participant.agent), selectinload(Participant.user))
        )
    ).scalars().all()
    out: dict[str, tuple[str | None, str]] = {}
    for p in rows:
        if p.agent is not None:
            out[p.id] = (p.agent_id, p.agent.name or "")
        elif p.user is not None:
            out[p.id] = (None, p.user.display_name or (p.user.email or "").split("@")[0])
        else:
            out[p.id] = (None, "")
    return out


async def _attempt_states(db: AsyncSession, request_ids: list[str]) -> dict[str, str]:
    if not request_ids:
        return {}
    rows = (
        await db.execute(
            select(AgentTurn.request_id, AgentTurn.state, AgentTurnAttempt.state)
            .outerjoin(
                AgentTurnAttempt,
                (AgentTurnAttempt.turn_id == AgentTurn.request_id)
                & (AgentTurnAttempt.attempt_number == AgentTurn.active_attempt),
            )
            .where(AgentTurn.request_id.in_(request_ids))
        )
    ).all()
    return {
        rid: (attempt_state or "") if turn_state in OPEN_TURN_STATES else turn_state
        for rid, turn_state, attempt_state in rows
    }


def _node_state(target: PeerAskTarget, attempt_state: str | None) -> str:
    if target.state in TERMINAL_TARGET_STATES:
        return target.state
    if target.awaiting_group_id is not None:
        return "waiting"
    if attempt_state in {"leased", "started"}:
        return "answering"
    return "pending"


async def _open_wake_turns(db: AsyncSession, groups: list[PeerAskGroup]) -> set[str]:
    rids = [g.wake_request_id for g in groups if g.wake_request_id]
    if not rids:
        return set()
    return set(
        (
            await db.scalars(
                select(AgentTurn.request_id).where(
                    AgentTurn.request_id.in_(rids),
                    AgentTurn.state.in_(OPEN_TURN_STATES),
                )
            )
        ).all()
    )


async def describe_chain(db: AsyncSession, root: PeerAskGroup) -> dict[str, Any]:
    """The chain rooted at *root* as the ``PeerChain`` API shape."""
    groups = list(
        (
            await db.scalars(
                select(PeerAskGroup)
                .where(PeerAskGroup.chain_id == root.chain_id)
                .order_by(PeerAskGroup.created_at, PeerAskGroup.id)
            )
        ).all()
    )
    by_group = {g.id: g for g in groups}
    targets = list(
        (
            await db.scalars(
                select(PeerAskTarget)
                .where(PeerAskTarget.group_id.in_(by_group))
                .order_by(PeerAskTarget.created_at, PeerAskTarget.id)
            )
        ).all()
    )
    pids = {g.caller_participant_id for g in groups} | {
        t.target_participant_id for t in targets
    }
    names = await _names(db, pids)
    attempts = await _attempt_states(db, [t.request_id for t in targets])
    open_wakes = await _open_wake_turns(db, groups)

    nodes = []
    for t in targets:
        group = by_group[t.group_id]
        target_agent, target_name = names.get(t.target_participant_id, (None, ""))
        nodes.append({
            "id": t.id,
            "parent_id": group.parent_target_id,
            "depth": group.depth,
            "caller_participant_id": group.caller_participant_id,
            "caller_name": names.get(group.caller_participant_id, (None, ""))[1],
            "target_participant_id": t.target_participant_id,
            "target_agent_id": target_agent,
            "target_name": target_name,
            "question": t.question,
            "state": _node_state(t, attempts.get(t.request_id)),
            "reason": t.reason,
            "question_message_id": t.question_message_id,
            "reply_message_id": t.reply_message_id,
            "created_at": _iso(t.created_at),
            "finished_at": _iso(t.finished_at),
        })

    if any(g.state == "cancelled" for g in groups):
        state = "cancelled"
    elif any(g.state in ACTIVE_GROUP_STATES for g in groups) or open_wakes:
        state = "active"
    else:
        state = "done"
    finished_at = None
    if state != "active":
        stamps = [t.finished_at for t in targets if t.finished_at is not None]
        stamps += [g.woken_at for g in groups if g.woken_at is not None]
        finished_at = max(stamps) if stamps else None

    origin = await _origin_message(db, root)
    root_agent, root_name = names.get(root.caller_participant_id, (root.caller_agent_id, ""))
    return {
        "id": root.chain_id,
        "room_id": root.room_id,
        "thread_root_id": (origin.root_message_id or origin.id) if origin else None,
        "origin_message_id": origin.id if origin else None,
        "state": state,
        "started_at": _iso(root.created_at),
        "finished_at": _iso(finished_at),
        "max_depth": max((n["depth"] for n in nodes), default=0),
        "agent_count": len(
            {root_agent} | {n["target_agent_id"] for n in nodes} - {None}
        ),
        "call_count": len(nodes),
        "root": {
            "participant_id": root.caller_participant_id,
            "agent_id": root_agent,
            "name": root_name,
        },
        "nodes": nodes,
    }


async def list_chains(db: AsyncSession, *, room_id: str, limit: int) -> list[dict[str, Any]]:
    """The room's newest chains, newest first."""
    roots = (
        await db.scalars(
            select(PeerAskGroup)
            .where(
                PeerAskGroup.room_id == room_id,
                PeerAskGroup.id == PeerAskGroup.chain_id,
            )
            .order_by(PeerAskGroup.created_at.desc(), PeerAskGroup.id.desc())
            .limit(limit)
        )
    ).all()
    return [await describe_chain(db, root) for root in roots]


@dataclass
class ChainCancel:
    cancelled: list[Any]  # TurnCancelResult


async def cancel_chain(
    db: AsyncSession, *, room_id: str, chain_id: str, actor_user_id: str | None
) -> ChainCancel | None:
    """Stop every open turn of a chain and close its groups. Never commits.

    Returns ``None`` when the room has no such chain. Groups end
    ``cancelled``, so nobody is woken afterwards; questions still open
    settle as ``cancelled (user_cancelled)``.
    """
    from anygarden.turns.cancel import TurnCancelError, cancel_general_turn

    groups = list(
        (
            await db.scalars(
                select(PeerAskGroup).where(
                    PeerAskGroup.chain_id == chain_id, PeerAskGroup.room_id == room_id
                )
            )
        ).all()
    )
    if not groups:
        return None
    targets = list(
        (
            await db.scalars(
                select(PeerAskTarget).where(
                    PeerAskTarget.group_id.in_([g.id for g in groups])
                )
            )
        ).all()
    )
    request_ids: list[str] = []
    for g in groups:
        request_ids.append(g.caller_request_id)
        if g.wake_request_id:
            request_ids.append(g.wake_request_id)
    request_ids += [t.request_id for t in targets if t.state not in TERMINAL_TARGET_STATES]

    cancelled = []
    for rid in dict.fromkeys(request_ids):
        turn = await db.get(AgentTurn, rid)
        if turn is None or turn.state not in OPEN_TURN_STATES:
            continue
        try:
            result = await cancel_general_turn(db, rid, actor_user_id=actor_user_id)
        except TurnCancelError:
            # A reply already being delivered (or an execution-bound turn)
            # finishes on its own; the group below still stops the chain.
            continue
        if result.changed:
            cancelled.append(result)

    from anygarden.orchestration.peer_fanin import _now

    now = _now()
    for t in targets:
        if t.state not in TERMINAL_TARGET_STATES:
            t.awaiting_group_id = None
            _settle(t, "cancelled", USER_CANCELLED, now)
    for g in groups:
        if g.state in {"collecting", "ready"}:
            g.state = "cancelled"
    await db.flush()
    return ChainCancel(cancelled=cancelled)
