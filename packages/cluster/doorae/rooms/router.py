"""REST endpoints for Room CRUD — ``/api/v1/rooms``."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from doorae.auth.dependencies import Identity, GuestClaims
from doorae.db.models import Agent, Participant, Room, User
from doorae.dependencies import (
    forbid_guest,
    get_admin_identity,
    get_current_identity,
    get_db,
)
from doorae.rooms.service import archive_child_rooms, create_sub_room

router = APIRouter(prefix="/api/v1/rooms", tags=["rooms"])


# -- Request / Response schemas -----------------------------------------------


class RoomCreate(BaseModel):
    project_id: str
    name: str
    description: Optional[str] = None
    parent_room_id: Optional[str] = None
    is_dm: bool = False


class ParticipantAdd(BaseModel):
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    role: str = "member"


class SubRoomCreate(BaseModel):
    name: str
    description: Optional[str] = None
    participants: list[str] = []
    is_dm: bool = False
    creator_participant_id: str


class RoomOut(BaseModel):
    id: str
    project_id: str
    name: str
    description: Optional[str] = None
    parent_room_id: Optional[str] = None
    is_dm: bool
    representative_agent_id: Optional[str] = None
    model_config = {"from_attributes": True}


class ParticipantOut(BaseModel):
    id: str
    room_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    role: str
    display_name: str = ""
    kind: str = "user"
    model_config = {"from_attributes": True}


class RoomDetailOut(RoomOut):
    participants: list[ParticipantOut] = []


# -- Endpoints ----------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RoomOut)
async def create_room(
    body: RoomCreate,
    # Guests can't create rooms — see §11.5 permission matrix.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Create a new room in a project."""
    room = Room(
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        parent_room_id=body.parent_room_id,
        is_dm=body.is_dm,
    )
    db.add(room)
    await db.flush()

    if identity.kind == "user":
        db.add(Participant(room_id=room.id, user_id=identity.id, role="admin"))
    elif identity.kind == "agent":
        db.add(Participant(room_id=room.id, agent_id=identity.id, role="admin"))

    await db.commit()
    await db.refresh(room)
    return room


@router.get("", response_model=list[RoomOut])
async def list_rooms(
    project_id: Optional[str] = None,
    is_dm: Optional[bool] = None,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """List rooms, optionally filtered by project and/or DM status.

    For guests this returns at most the single room their JWT is
    bound to (§11.5 "다른 룸 조회 ❌"). Registered users still see
    the full tree the endpoint used to return — project-/dm-level
    filtering is unchanged.
    """
    query = select(Room)
    if identity.kind == "guest" and isinstance(identity.claims, GuestClaims):
        # Explicit room-id pin — even a spoofed ``project_id`` query
        # parameter cannot widen the result set beyond the JWT's
        # single-room binding.
        query = query.where(Room.id == identity.claims.room_id)
    if project_id is not None:
        query = query.where(Room.project_id == project_id)
    if is_dm is not None:
        query = query.where(Room.is_dm == is_dm)
    query = query.order_by(Room.created_at)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{room_id}", response_model=RoomDetailOut)
async def get_room(
    room_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Get room details including participants with display names.

    Guests may only read the room their JWT is bound to. We check
    the claim BEFORE the DB lookup so a guest can't learn whether
    an unrelated room id exists by comparing 403 vs 404.
    """
    if identity.kind == "guest":
        if (
            not isinstance(identity.claims, GuestClaims)
            or identity.claims.room_id != room_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guest token bound to a different room",
            )

    result = await db.execute(
        select(Room)
        .where(Room.id == room_id)
        .options(selectinload(Room.participants))
    )
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    participant_outs: list[ParticipantOut] = []
    for p in room.participants:
        display_name = ""
        kind = "user"
        if p.user_id:
            user_result = await db.execute(select(User).where(User.id == p.user_id))
            user = user_result.scalar_one_or_none()
            if user:
                # Guests have no email; prefer their supplied display_name.
                # Registered users fall back to the local-part of their email
                # to preserve the current behaviour.
                if user.display_name:
                    display_name = user.display_name
                elif user.email:
                    display_name = user.email.split("@")[0]
                else:
                    display_name = "Guest"
            kind = "user"
        elif p.agent_id:
            agent_result = await db.execute(select(Agent).where(Agent.id == p.agent_id))
            agent = agent_result.scalar_one_or_none()
            if agent:
                display_name = agent.name
            kind = "agent"
        participant_outs.append(ParticipantOut(
            id=p.id,
            room_id=p.room_id,
            user_id=p.user_id,
            agent_id=p.agent_id,
            role=p.role,
            display_name=display_name,
            kind=kind,
        ))

    return RoomDetailOut(
        id=room.id,
        project_id=room.project_id,
        name=room.name,
        parent_room_id=room.parent_room_id,
        is_dm=room.is_dm,
        representative_agent_id=room.representative_agent_id,
        participants=participant_outs,
    )


@router.post("/{room_id}/participants", status_code=201, response_model=ParticipantOut)
async def add_participant(
    room_id: str,
    body: ParticipantAdd,
    request: Request,
    # Mutating room membership is closed to guests. The guest flow
    # adds them via ``POST /auth/guest`` instead.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Add a participant (user or agent) to a room."""
    # Verify room exists
    result = await db.execute(select(Room).where(Room.id == room_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Room not found")

    if not body.user_id and not body.agent_id:
        raise HTTPException(status_code=400, detail="Must provide user_id or agent_id")

    participant = Participant(
        room_id=room_id,
        user_id=body.user_id,
        agent_id=body.agent_id,
        role=body.role,
    )
    db.add(participant)
    await db.commit()
    await db.refresh(participant)

    # Notify the newly added participant through any of their existing
    # WS connections so the client can react immediately without polling.
    #
    # - Agents receive ``JoinRoomOut`` and the SDK auto-connects to the
    #   new room (see doorae_agent/client.py:298-302). Without this they
    #   would miss messages until their next full reconnect.
    # - Users receive ``RoomMembershipChangedOut`` so the frontend
    #   sidebar/room-list can refresh. Users don't auto-join WS; they
    #   open it lazily when they navigate to the room.
    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        if body.agent_id:
            from doorae.ws.protocol import JoinRoomOut

            other_pids = (
                await db.execute(
                    select(Participant.id).where(
                        Participant.agent_id == body.agent_id,
                        Participant.id != participant.id,
                    )
                )
            ).scalars().all()
            join_frame = JoinRoomOut(room_id=room_id, participant_id="")
            for pid in other_pids:
                await manager.send_to(pid, join_frame)
        elif body.user_id:
            from doorae.ws.protocol import RoomMembershipChangedOut

            other_pids = (
                await db.execute(
                    select(Participant.id).where(
                        Participant.user_id == body.user_id,
                        Participant.id != participant.id,
                    )
                )
            ).scalars().all()
            user_frame = RoomMembershipChangedOut(
                action="added",
                room_id=room_id,
                user_id=body.user_id,
            )
            for pid in other_pids:
                await manager.send_to(pid, user_frame)

    return participant


class RoomUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


@router.patch("/{room_id}", response_model=RoomOut)
async def update_room(
    room_id: str,
    body: RoomUpdate,
    # Room metadata changes are closed to guests (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Update room name and/or description."""
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if body.name is not None:
        room.name = body.name
    if body.description is not None:
        room.description = body.description
    await db.commit()
    await db.refresh(room)
    return room


class RepresentativeSet(BaseModel):
    agent_id: Optional[str] = None


@router.put("/{room_id}/representative")
async def set_representative(
    room_id: str,
    body: RepresentativeSet,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Set or clear the representative agent for a room. Admin only."""
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    if body.agent_id is not None:
        # Verify agent is a participant of this room
        stmt = select(Participant).where(
            Participant.room_id == room_id,
            Participant.agent_id == body.agent_id,
        )
        part = (await db.execute(stmt)).scalar_one_or_none()
        if part is None:
            raise HTTPException(
                status_code=400,
                detail="Agent is not a participant of this room",
            )

    room.representative_agent_id = body.agent_id
    await db.commit()
    await db.refresh(room)
    return {"room_id": room.id, "representative_agent_id": room.representative_agent_id}


@router.delete("/{room_id}", status_code=204)
async def delete_room(
    room_id: str,
    # Guests can't delete rooms (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Delete a room, cascading: archive child rooms."""
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    await archive_child_rooms(db, room_id)
    await db.execute(delete(Participant).where(Participant.room_id == room_id))
    await db.delete(room)
    await db.commit()
    return None


@router.post("/{room_id}/stop-agents")
async def stop_all_agents_in_room(
    room_id: str,
    request: Request,
    # Guests can't control agent lifecycle (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Stop all running agents in a room."""
    result = await db.execute(select(Room).where(Room.id == room_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Room not found")

    # Find all agent participants in this room
    stmt = (
        select(Participant)
        .where(Participant.room_id == room_id)
        .where(Participant.agent_id.isnot(None))
    )
    participants = (await db.execute(stmt)).scalars().all()
    agent_ids = [p.agent_id for p in participants]

    lifecycle = request.app.state.agent_lifecycle
    stopped = []
    for aid in agent_ids:
        agent_result = await db.execute(select(Agent).where(Agent.id == aid))
        agent = agent_result.scalar_one_or_none()
        if agent and agent.actual_state in ("running", "starting", "pending"):
            await lifecycle.request_stop(agent.id)
            agent.desired_state = "stopped"
            stopped.append(agent.id)

    await db.commit()
    return {"stopped": stopped, "count": len(stopped)}


@router.get("/{room_id}/sub-rooms", response_model=list[RoomOut])
async def list_sub_rooms(
    room_id: str,
    name: str | None = None,
    # Sub-room listing is a tree-nav feature — guests live in a
    # single flat room (§11.5) and so cannot enumerate children.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """List sub-rooms of a room, optionally filtered by name."""
    query = select(Room).where(Room.parent_room_id == room_id)
    if name:
        query = query.where(Room.name == name)
    query = query.order_by(Room.created_at)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/{room_id}/sub-rooms", status_code=201, response_model=RoomOut)
# NOTE (PR C): the guest branch is deliberately handled at the
# ``identity`` dependency below (``forbid_guest``). Sub-room
# creation is a membership-expanding operation and is therefore
# closed to guests regardless of their room scope.
async def create_sub_room_endpoint(
    room_id: str,
    body: SubRoomCreate,
    request: Request,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Create a sub-room under an existing room with permission inheritance."""
    child = await create_sub_room(
        db,
        parent_room_id=room_id,
        name=body.name,
        description=body.description,
        participants=body.participants,
        is_dm=body.is_dm,
        creator_participant_id=body.creator_participant_id,
    )
    await db.commit()
    await db.refresh(child)

    # Notify all participants in the parent room so agent SDKs
    # can dynamically join the new sub-room. broadcast is more
    # reliable than send_to — it reaches all active WS connections
    # in the room even if _by_participant was overwritten. Non-agent
    # clients (browser frontend) silently ignore the join_room frame.
    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        from doorae.ws.protocol import JoinRoomOut
        frame = JoinRoomOut(room_id=child.id, participant_id="")
        await manager.broadcast(room_id, frame)

    return child
