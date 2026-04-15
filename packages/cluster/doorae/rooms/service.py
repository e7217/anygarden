"""Room service — sub-room creation with permission inheritance."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Participant, Room


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
