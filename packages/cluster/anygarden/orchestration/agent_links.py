"""Agent links: whom an agent may ask, from any room (#1006).

A room used to decide who works together: an agent could ask only the
peers seated in the same room, so a different team meant a new room or
sub-room. A link is global and one-directional — "A may ask B" —
so whichever agent a person talks to brings its own links along.

``ask_peer`` reaches a linked agent that is not in the room by seating it
for the question's thread only (:func:`seat_linked_agent`). Such a
participant is marked ``joined_via = "link"`` and stays out of everything
that addresses the room's members (:func:`visible_member`).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, AgentLink, Participant
from anygarden.ws.protocol import LinkBrief

JOINED_VIA_LINK = "link"


def visible_member():
    """SQL filter for participants that are members of the room itself."""
    return Participant.joined_via.is_(None)


async def link_exists(
    db: AsyncSession, *, from_agent_id: str, to_agent_id: str
) -> bool:
    return (
        await db.scalar(
            select(AgentLink.id).where(
                AgentLink.from_agent_id == from_agent_id,
                AgentLink.to_agent_id == to_agent_id,
            )
        )
    ) is not None


async def links_by_agent(
    db: AsyncSession, agent_ids: Iterable[str]
) -> dict[str, list[LinkBrief]]:
    """Outgoing links of *agent_ids*, each list ordered by target name."""
    ids = list(dict.fromkeys(agent_ids))
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(AgentLink, Agent)
            .join(Agent, Agent.id == AgentLink.to_agent_id)
            .where(AgentLink.from_agent_id.in_(ids))
            .order_by(Agent.name, Agent.id)
        )
    ).all()
    out: dict[str, list[LinkBrief]] = {}
    for link, target in rows:
        out.setdefault(link.from_agent_id, []).append(
            LinkBrief(
                agent_id=target.id,
                display_name=target.name or "",
                description=target.description,
            )
        )
    return out


async def room_agent_links(
    db: AsyncSession, *, room_id: str
) -> dict[str, list[LinkBrief]]:
    """Outgoing links of every agent seated in *room_id*."""
    agent_ids = (
        await db.scalars(
            select(Participant.agent_id).where(
                Participant.room_id == room_id, Participant.agent_id.isnot(None)
            )
        )
    ).all()
    return await links_by_agent(db, agent_ids)


async def seat_linked_agent(
    db: AsyncSession, *, room_id: str, agent_id: str
) -> tuple[Participant, bool]:
    """Seat *agent_id* in *room_id* for a linked question (DB only).

    Returns the participant and whether it was inserted. An agent already
    in the room keeps its row (and its membership). Unlike a member, the
    new row never fills the representative slot.
    """
    existing = (
        await db.execute(
            select(Participant).where(
                Participant.room_id == room_id, Participant.agent_id == agent_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    participant = Participant(
        room_id=room_id, agent_id=agent_id, role="member", joined_via=JOINED_VIA_LINK
    )
    db.add(participant)
    await db.flush()
    return participant, True
