"""Room participant roster: assembly and runtime propagation (#221 / #644).

The roster is the list an agent's LLM reads to learn who else is in the
room — ``- {name} (id: {uuid}, kind: {kind}) — {description}`` — and the
lookup the agent SDK uses to label an incoming message with its actual
sender (#538). Two consumers, one source of truth.

#221 shipped it in the WS ``welcome`` frame only, which made it a
connect-time snapshot: a peer that joined afterwards stayed invisible, a
peer that left lingered, and an edited ``description`` never reached
anyone. #644 adds :func:`broadcast_roster`, which every mutation that
changes a rendered roster line calls to push a fresh snapshot to the
room.

This module sits below both ``ws.handler`` (welcome assembly) and
``rooms.router`` / ``api.v1.agents`` (the mutation sites) so all three
share one implementation without an import cycle — ``ws.handler``
already imports ``rooms.membership``, so hosting the helper there would
close the loop.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from anygarden.db.models import Participant
from anygarden.ws.manager import ConnectionManager
from anygarden.ws.protocol import ParticipantBrief, RoomSettingsChangedOut

logger = structlog.get_logger(__name__)


async def build_participants_brief(
    db: AsyncSession, *, room_id: str
) -> list[ParticipantBrief]:
    """Collect a room's roster (#221).

    Ordered by ``joined_at`` then ``id`` to match
    ``_compute_round_robin_next``'s stable order.

    ``selectinload`` keeps this to a small fixed number of queries
    regardless of roster size. Orphaned participants (both FK relations
    empty — the transient state between a user deletion and the
    cascaded participant row cleanup) fall back to a generic label
    rather than raising, because welcome must succeed even for a
    temporarily inconsistent row.
    """
    stmt = (
        select(Participant)
        .where(Participant.room_id == room_id)
        .options(
            selectinload(Participant.user),
            selectinload(Participant.agent),
        )
        .order_by(Participant.joined_at.asc(), Participant.id.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    briefs: list[ParticipantBrief] = []
    for p in rows:
        if p.user is not None:
            user = p.user
            if user.display_name:
                name = user.display_name
            elif user.email:
                name = user.email.split("@")[0]
            else:
                name = "Guest"
            kind = "guest" if user.is_anonymous else "user"
            briefs.append(ParticipantBrief(id=p.id, display_name=name, kind=kind))
        elif p.agent is not None:
            briefs.append(
                ParticipantBrief(
                    id=p.id,
                    display_name=p.agent.name or "",
                    kind="agent",
                    agent_id=p.agent_id,
                    description=p.agent.description,
                )
            )
        else:
            briefs.append(
                ParticipantBrief(id=p.id, display_name="Unknown", kind="user")
            )
    return briefs


async def broadcast_roster(
    manager: ConnectionManager | None,
    db: AsyncSession,
    *,
    room_id: str,
) -> None:
    """Push the room's current roster to every subscriber (#644).

    Rides ``RoomSettingsChangedOut`` because that frame already carries
    "values cached at welcome that can change later" (speaker strategy,
    ephemeral flag) to exactly this audience. Settings fields are left
    ``None`` so a roster refresh never resets a receiver's cached
    dispatch mode.

    A ``None`` *manager* (unit tests without a wired connection
    manager) skips the push, matching the convention in
    ``rooms.membership``.
    """
    if manager is None:
        return
    participants = await build_participants_brief(db, room_id=room_id)
    await manager.broadcast(
        room_id,
        RoomSettingsChangedOut(room_id=room_id, participants=participants),
    )
    logger.info(
        "ws.roster_broadcast", room_id=room_id, participants=len(participants)
    )


async def broadcast_roster_for_agent(
    manager: ConnectionManager | None,
    db: AsyncSession,
    *,
    agent_id: str,
) -> None:
    """Refresh the roster in every room *agent_id* participates in.

    Used by ``PUT /api/v1/agents/{id}`` when a field the roster renders
    (``name``, ``description``) changes: peers across all of the
    agent's rooms hold a copy of that line, so each room needs its own
    snapshot. Agents typically sit in a handful of rooms, so the fan-out
    stays small; if that stops holding, batch the sends here rather than
    at the call site.
    """
    if manager is None:
        return
    room_ids = (
        await db.execute(
            select(Participant.room_id).where(Participant.agent_id == agent_id)
        )
    ).scalars().all()
    for room_id in room_ids:
        await broadcast_roster(manager, db, room_id=room_id)
