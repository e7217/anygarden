"""Room service — sub-room creation with permission inheritance."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select, update  # noqa: F401  (update used by future PRs)
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Participant, Room


# Sparse integer spacing for ``Participant.sort_order``. A gap of
# 1024 between neighbors means ~10 mid-list insertions are possible
# before the gap collapses to 1 and a full renumber is needed.
PIN_ORDER_STEP = 1024


async def create_sub_room(
    db: AsyncSession,
    *,
    parent_room_id: str,
    name: str,
    description: str | None = None,
    participants: list[str],
    is_dm: bool = False,
    creator_participant_id: str,
) -> tuple[Room, set[str]]:
    """Create a child room under *parent_room_id*.

    Returns ``(child, agent_ids)`` where ``agent_ids`` is the set of
    agent IDs that were added as Participants of the new sub-room. The
    router layer uses this set to push a ``JoinRoomOut`` to each of
    those agents' *other* WS sessions so their SDK can auto-subscribe
    to the new room — a broadcast over the parent room (the previous
    approach) would miss agents that weren't parent-subscribers.

    Enforces:
    - Self-reference prevention: parent must differ from the new room.
    - Permission inheritance: the creator must be a member of the parent room.
    - All listed participants must also be members of the parent room.
    """
    # Fetch parent room
    result = await db.execute(select(Room).where(Room.id == parent_room_id))
    parent = result.scalar_one_or_none()
    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parent room not found",
        )

    # Verify creator is a member of the parent room
    result = await db.execute(
        select(Participant).where(
            Participant.room_id == parent_room_id,
            Participant.id == creator_participant_id,
        )
    )
    creator_in_parent = result.scalar_one_or_none()
    if creator_in_parent is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creator is not a member of the parent room",
        )

    # Verify all requested participants are members of the parent room
    if participants:
        result = await db.execute(
            select(Participant.id).where(
                Participant.room_id == parent_room_id,
                Participant.id.in_(participants),
            )
        )
        found_ids = {row[0] for row in result.all()}
        missing = set(participants) - found_ids
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Participants not in parent room: {missing}",
            )

    # Create the sub-room
    child = Room(
        project_id=parent.project_id,
        name=name,
        description=description,
        parent_room_id=parent_room_id,
        is_dm=is_dm,
    )
    db.add(child)
    await db.flush()

    # Self-reference prevention CHECK
    if child.id == parent_room_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A room cannot be its own parent",
        )

    agent_ids: set[str] = set()

    # Add creator as first participant
    creator_part = Participant(
        room_id=child.id,
        user_id=creator_in_parent.user_id,
        agent_id=creator_in_parent.agent_id,
        role="owner",
    )
    db.add(creator_part)
    if creator_in_parent.agent_id:
        agent_ids.add(creator_in_parent.agent_id)

    # Add other participants, inheriting their user/agent IDs from parent
    for pid in participants:
        if pid == creator_participant_id:
            continue
        result = await db.execute(
            select(Participant).where(
                Participant.id == pid,
                Participant.room_id == parent_room_id,
            )
        )
        parent_part = result.scalar_one_or_none()
        if parent_part:
            new_part = Participant(
                room_id=child.id,
                user_id=parent_part.user_id,
                agent_id=parent_part.agent_id,
                role="member",
            )
            db.add(new_part)
            if parent_part.agent_id:
                agent_ids.add(parent_part.agent_id)

    await db.flush()
    await db.refresh(child)
    return child, agent_ids


async def _load_pinned_order(
    db: AsyncSession, *, user_id: str
) -> list[str]:
    """Return ``user_id``'s pinned ``room_id`` list in sidebar order."""
    result = await db.execute(
        select(Participant.room_id)
        .where(
            Participant.user_id == user_id,
            Participant.pinned.is_(True),
        )
        .order_by(Participant.sort_order.asc())
    )
    return [row[0] for row in result.all()]


async def set_room_pinned(
    db: AsyncSession,
    *,
    user_id: str,
    room_id: str,
    pinned: bool,
) -> list[str]:
    """Toggle the sidebar pin state for ``user_id``'s participation in ``room_id``.

    When *pinned* flips to ``True`` the row lands at the tail of the
    pinned section (``max(sort_order) + PIN_ORDER_STEP``). Flipping
    back to ``False`` clears ``sort_order`` so the room rejoins the
    default section.

    Returns the user's current pinned ``room_id`` list in sidebar
    order.
    """
    # Find the user's Participant row in this room. Agent-only rooms
    # (no user_id on any participant) and non-member rooms both hit
    # this 404 branch.
    result = await db.execute(
        select(Participant).where(
            Participant.user_id == user_id,
            Participant.room_id == room_id,
        )
    )
    part = result.scalar_one_or_none()
    if part is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found or user is not a participant",
        )

    if pinned:
        if not part.pinned:
            # Place at the tail of the current pinned section.
            tail_result = await db.execute(
                select(Participant.sort_order)
                .where(
                    Participant.user_id == user_id,
                    Participant.pinned.is_(True),
                )
                .order_by(Participant.sort_order.desc())
                .limit(1)
            )
            current_tail = tail_result.scalar_one_or_none()
            next_order = (
                PIN_ORDER_STEP
                if current_tail is None
                else current_tail + PIN_ORDER_STEP
            )
            part.pinned = True
            part.sort_order = next_order
    else:
        part.pinned = False
        part.sort_order = None

    await db.flush()
    return await _load_pinned_order(db, user_id=user_id)


async def reorder_pinned_rooms(
    db: AsyncSession,
    *,
    user_id: str,
    room_ids: list[str],
) -> list[str]:
    """Rewrite ``sort_order`` for ``user_id``'s pinned rooms to match ``room_ids``.

    *room_ids* is a full snapshot of the pinned section in its new
    order — only entries that are currently pinned for *user_id* are
    renumbered, so stray ids do not promote anything. Pinned rows
    not present in *room_ids* keep their existing ``sort_order``
    (safety net against accidental unpinning through a partial
    payload).

    Returns the new pinned ``room_id`` order after the write.
    """
    # Load the user's pinned participations keyed by room_id so we can
    # rewrite the ones listed in *room_ids* without extra round-trips.
    result = await db.execute(
        select(Participant).where(
            Participant.user_id == user_id,
            Participant.pinned.is_(True),
        )
    )
    by_room = {p.room_id: p for p in result.scalars().all()}

    position = 0
    for room_id in room_ids:
        part = by_room.get(room_id)
        if part is None:
            # Caller sent a room the user isn't currently pinning —
            # ignore rather than auto-promote.
            continue
        position += 1
        part.sort_order = position * PIN_ORDER_STEP

    await db.flush()
    return await _load_pinned_order(db, user_id=user_id)


async def archive_child_rooms(db: AsyncSession, room_id: str) -> int:
    """Cascade-archive all child rooms when a parent room is deleted.

    Returns the number of child rooms archived.
    """
    result = await db.execute(
        select(Room).where(Room.parent_room_id == room_id)
    )
    children = list(result.scalars().all())
    count = 0
    for child in children:
        # Recursively archive grandchildren
        count += await archive_child_rooms(db, child.id)
        # Detach from parent (archive behavior)
        child.parent_room_id = None
        count += 1
    return count
