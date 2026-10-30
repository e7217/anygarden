"""The conversation an agent missed, attached to its delivered turn (#866).

An engine session is scoped to one conversation — the room's main channel or
one thread — and only holds what was fed to it on its own turns. Everything
said there between two of the agent's turns used to reach it only through a
small in-process buffer (10 lines, 10 minutes, lost on respawn), and
messages addressed to someone else not at all.

The server knows exactly what the session has seen: the trigger of the
agent's previous *completed* turn in the same conversation. So at delivery
it attaches the messages after that point and before this turn's trigger.
Without a previous turn (a new conversation for this agent) it attaches the
recent tail, the agent's own messages included.

A delivery that runs in a fresh engine session (#984) — an operating room's
lead, whose unbound turns never resume, or a retry — holds none of its
earlier turns, so it gets that recent tail every time.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, AgentTurn, Message, Room
from anygarden.messages import agent_view

MAX_CONTEXT_MESSAGES = 30
# Paid on every turn of a fresh-session agent, so tighter than a one-off
# catch-up.
FRESH_SESSION_CONTEXT_MESSAGES = 20


def _same_conversation(stmt, root_id: str | None):
    """Messages that *triggered* turns in this conversation's session.

    Stricter than :func:`agent_view.in_conversation`: a turn on a thread's
    root message ran in the main channel's session, not the thread's.
    """
    if root_id is None:
        return stmt.where(Message.root_message_id.is_(None))
    return stmt.where(Message.root_message_id == root_id)


async def build_conversation_context(
    db: AsyncSession, turn: AgentTurn, trigger: Message, *, fresh_session: bool = False
) -> dict[str, Any] | None:
    """What *turn*'s agent missed before *trigger*, or ``None`` for nothing.

    *fresh_session* says the engine session holds nothing from earlier
    turns, so no previous turn counts as already seen.
    """
    if turn.execution_id is not None or turn.agent_id is None:
        return None
    room = await db.get(Room, turn.room_id)
    if room is None or not room.context_window_enabled:
        return None
    agent = await db.get(Agent, turn.agent_id)
    if agent is None or agent.context_window_opt_out:
        return None

    root_id = trigger.root_message_id
    baseline = None if fresh_session else (
        await db.execute(
            _same_conversation(
                select(Message.seq)
                .join(AgentTurn, AgentTurn.trigger_message_id == Message.id)
                .where(
                    AgentTurn.agent_id == turn.agent_id,
                    AgentTurn.room_id == turn.room_id,
                    AgentTurn.state == "completed",
                    AgentTurn.request_id != turn.request_id,
                    Message.seq < trigger.seq,
                ),
                root_id,
            )
            .order_by(Message.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    window = agent_view.in_conversation(
        select(Message).where(
            Message.room_id == turn.room_id,
            Message.seq < trigger.seq,
            Message.seq > (baseline or 0),
        ),
        root_id,
    )
    if baseline is not None and turn.target_participant_id is not None:
        # Its own replies after the baseline are already in the session.
        window = window.where(
            (Message.participant_id != turn.target_participant_id)
            | Message.participant_id.is_(None)
        )
    rows = list(
        (
            await db.execute(
                window.order_by(Message.seq.desc()).limit(
                    FRESH_SESSION_CONTEXT_MESSAGES if fresh_session else MAX_CONTEXT_MESSAGES
                )
            )
        ).scalars().all()
    )
    rows.reverse()
    total = (
        await db.execute(select(func.count()).select_from(window.subquery()))
    ).scalar_one()
    if root_id is not None and baseline is None and all(m.id != root_id for m in rows):
        # Keep the thread's topic in view when the window cut it off.
        root = await db.get(Message, root_id)
        if root is not None:
            rows.insert(0, root)

    messages = await agent_view.describe(
        db, turn.room_id, rows,
        self_pids=[turn.target_participant_id] if turn.target_participant_id else [],
    )
    if not messages:
        return None
    return {
        "version": 1,
        "messages": messages,
        "omitted": max(0, total - len(rows)),
    }
