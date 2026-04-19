"""REST endpoints for Room CRUD — ``/api/v1/rooms``."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from doorae.api.v1.invites import _require_room_admin_or_owner
from doorae.auth.dependencies import Identity, GuestClaims
from doorae.db.models import Agent, Participant, Room, User
from doorae.dependencies import (
    forbid_guest,
    get_admin_identity,
    get_current_identity,
    get_db,
)
from doorae.rooms.membership import add_user_to_room, ensure_agent_in_room
from doorae.rooms.service import (
    archive_child_rooms,
    create_sub_room,
    reorder_pinned_rooms,
    set_room_pinned,
)

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
    # Caller-specific sidebar pin state (#47). Populated by
    # ``list_rooms`` for registered users; guests and agent callers
    # always see ``pinned=False`` / ``sort_order=None`` because the
    # sidebar doesn't apply to them.
    pinned: bool = False
    sort_order: Optional[int] = None
    # #148 — when True the server stamps ``metadata.ingest_only=True``
    # on ambient (un-addressed) broadcasts so peer agents pick up the
    # text as background context. Part 1 only surfaces storage; Part 3
    # wires the broadcast-side logic.
    context_window_enabled: bool = False
    model_config = {"from_attributes": True}


class ParticipantOut(BaseModel):
    id: str
    room_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    role: str
    display_name: str = ""
    kind: str = "user"
    # True when the underlying User row is an anonymous guest. Lets
    # the client render a distinct "Guest" badge without having to
    # keep a separate kind value (the server would otherwise risk
    # breaking callers that assume kind ∈ {"user", "agent"}).
    is_anonymous: bool = False
    # Presence fields (#54). ``online`` tracks whether the participant
    # currently has an open WS subscription; ``last_seen_at`` exposes
    # the most recent liveness signal (WS disconnect or agent
    # heartbeat) so the UI can render "last seen N min ago" tooltips.
    # Defaulted so legacy clients that build ParticipantOut without
    # presence info keep working.
    online: bool = False
    last_seen_at: Optional[datetime] = None
    # Agent engine identifier (#102) — 'claude-code', 'codex',
    # 'gemini-cli', 'openhands', 'deep-agents', etc. Populated when
    # ``kind == 'agent'`` from the backing ``Agent.engine`` row; None
    # for user/guest rows. Drives the engine-mark badge on
    # ``EntityAvatar`` so non-admin viewers see it too — the
    # admin-gated ``useAgents()`` hook is not available to guests
    # or regular users.
    engine: Optional[str] = None
    # Issue #101 — agent avatar override (null/null for user rows).
    # Lets MessageBubble / ParticipantListPopover render the admin's
    # chosen emoji / lucide icon without a second lookup against
    # ``/api/v1/agents``.
    avatar_kind: Optional[str] = None
    avatar_value: Optional[str] = None
    model_config = {"from_attributes": True}


class RoomDetailOut(RoomOut):
    participants: list[ParticipantOut] = []


class PinRoomBody(BaseModel):
    pinned: bool


class PinOrderBody(BaseModel):
    # Snapshot of the caller's pinned sidebar section in its new
    # order. Idempotent: resending the same list is a no-op. Only
    # room_ids that are currently pinned for the caller are renumbered.
    room_ids: list[str]


class PinOrderOut(BaseModel):
    pinned_room_ids: list[str]


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
    rooms = list(result.scalars().all())

    # Enrich with caller-specific pin state so the sidebar doesn't
    # need a second round-trip on every load. Only meaningful for
    # registered users — the other identity kinds keep the default
    # ``pinned=False``.
    pin_state: dict[str, tuple[bool, Optional[int]]] = {}
    if identity.kind == "user" and rooms:
        room_ids = [r.id for r in rooms]
        pin_result = await db.execute(
            select(
                Participant.room_id,
                Participant.pinned,
                Participant.sort_order,
            ).where(
                Participant.user_id == identity.id,
                Participant.room_id.in_(room_ids),
            )
        )
        for row in pin_result.all():
            pin_state[row[0]] = (bool(row[1]), row[2])

    out: list[RoomOut] = []
    for r in rooms:
        pinned, sort_order = pin_state.get(r.id, (False, None))
        out.append(
            RoomOut(
                id=r.id,
                project_id=r.project_id,
                name=r.name,
                description=r.description,
                parent_room_id=r.parent_room_id,
                is_dm=r.is_dm,
                representative_agent_id=r.representative_agent_id,
                pinned=pinned,
                sort_order=sort_order,
                context_window_enabled=r.context_window_enabled,
            )
        )
    return out


@router.get("/{room_id}", response_model=RoomDetailOut)
async def get_room(
    room_id: str,
    request: Request,
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

    # Presence snapshot (#54) — batch resolve online/last_seen_at for
    # every participant in a single call so we don't round-trip per
    # row. Falls back silently if the presence service isn't wired
    # (e.g. tests that construct ``create_app`` without running the
    # lifespan): participants still serialize with default offline.
    presence_by_pid: dict[str, tuple[bool, Optional[datetime]]] = {}
    presence = getattr(request.app.state, "presence_service", None)
    if presence is not None:
        snapshot = await presence.room_snapshot(room_id, db=db)
        presence_by_pid = {
            s.participant_id: (s.online, s.last_seen_at) for s in snapshot
        }

    participant_outs: list[ParticipantOut] = []
    for p in room.participants:
        display_name = ""
        kind = "user"
        is_anonymous = False
        engine: Optional[str] = None
        avatar_kind: Optional[str] = None
        avatar_value: Optional[str] = None
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
                is_anonymous = bool(user.is_anonymous)
            kind = "user"
        elif p.agent_id:
            agent_result = await db.execute(select(Agent).where(Agent.id == p.agent_id))
            agent = agent_result.scalar_one_or_none()
            if agent:
                display_name = agent.name
                engine = agent.engine
                # Issue #101 — carry the admin's avatar choice so the
                # room renders the same glyph everywhere the agent
                # appears, without a second /agents round-trip.
                avatar_kind = agent.avatar_kind
                avatar_value = agent.avatar_value
            kind = "agent"
        online, last_seen_at = presence_by_pid.get(p.id, (False, None))
        participant_outs.append(ParticipantOut(
            id=p.id,
            room_id=p.room_id,
            user_id=p.user_id,
            agent_id=p.agent_id,
            role=p.role,
            display_name=display_name,
            kind=kind,
            is_anonymous=is_anonymous,
            online=online,
            last_seen_at=last_seen_at,
            engine=engine,
            avatar_kind=avatar_kind,
            avatar_value=avatar_value,
        ))

    return RoomDetailOut(
        id=room.id,
        project_id=room.project_id,
        name=room.name,
        parent_room_id=room.parent_room_id,
        is_dm=room.is_dm,
        representative_agent_id=room.representative_agent_id,
        context_window_enabled=room.context_window_enabled,
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

    # Both branches funnel through ``doorae.rooms.membership`` so the
    # Participant-insert and its matching WS notification (JoinRoomOut
    # for agents, RoomMembershipChangedOut for users) stay in lockstep
    # — see issue #50 for the history of this invariant drifting.
    manager = getattr(request.app.state, "connection_manager", None)
    if body.agent_id:
        participant, _ = await ensure_agent_in_room(
            db,
            manager,
            room_id=room_id,
            agent_id=body.agent_id,
            role=body.role,
        )
    else:
        assert body.user_id is not None
        participant = await add_user_to_room(
            db,
            manager,
            room_id=room_id,
            user_id=body.user_id,
            role=body.role,
        )

    return participant


@router.delete(
    "/{room_id}/participants/{participant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_participant(
    room_id: str,
    participant_id: str,
    request: Request,
    # Guests cannot manage membership (§11.5). ``forbid_guest`` yields
    # a user or agent identity; ``_require_room_admin_or_owner`` below
    # further restricts to global admins or room admin/owner roles.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Remove a participant (user or agent) from a room.

    Authorisation is 403-before-404: non-admin non-members get 403
    regardless of whether the participant or room exists, mirroring
    the invites endpoint policy (see api/v1/invites.py).

    Guard rails:
    - Caller cannot remove themselves — they should leave via a
      dedicated flow. Returns 400 with ``"Use leave-room instead"``.
    - Removing the last admin/owner in a room is refused with 409 to
      avoid orphaning the room.
    - If the removed participant is the room's representative agent,
      ``Room.representative_agent_id`` is cleared atomically in the
      same transaction to avoid dangling references.

    Invite revocation for removed guests is intentionally NOT
    performed here: ``require_room_member`` on the WS side will reject
    any further action from a user with no Participant row, so the
    guest session is effectively neutralised by the DELETE alone.

    ``Message.participant_id`` is ``ON DELETE SET NULL`` (migration
    004) so chat history is preserved when a participant is removed.
    """
    # 1. Authz FIRST — do not leak room/participant existence to
    #    callers who would 403 regardless. Matches invites.py ordering.
    await _require_room_admin_or_owner(room_id, identity, db)

    # 2. Load the target participant row.
    target_stmt = select(Participant).where(
        Participant.id == participant_id,
        Participant.room_id == room_id,
    )
    target = (await db.execute(target_stmt)).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Participant not found")

    # 3. Self-removal guard — direct this to the (future) leave flow.
    if (
        identity.kind == "user"
        and target.user_id is not None
        and target.user_id == identity.id
    ) or (
        identity.kind == "agent"
        and target.agent_id is not None
        and target.agent_id == identity.id
    ):
        raise HTTPException(status_code=400, detail="Use leave-room instead")

    # 4. Last-admin guard. Prevent orphaning a room by checking
    #    whether removing this row would leave zero admin/owner
    #    participants. We count admin/owner rows in the room and
    #    compute the post-delete count inline rather than running a
    #    second query — SQLite/PG both promise snapshot consistency
    #    within this session.
    if target.role in ("admin", "owner"):
        admin_count_stmt = select(Participant).where(
            Participant.room_id == room_id,
            Participant.role.in_(("admin", "owner")),
        )
        admins = (await db.execute(admin_count_stmt)).scalars().all()
        remaining = sum(1 for a in admins if a.id != target.id)
        if remaining == 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot remove the last admin/owner of the room",
            )

    # 5. If the removed participant is the room's representative
    #    agent, clear the field BEFORE deleting the row — keeps the
    #    FK invariant even if the DB skips the SET NULL cascade.
    if target.agent_id is not None:
        room_stmt = select(Room).where(Room.id == room_id)
        room = (await db.execute(room_stmt)).scalar_one_or_none()
        if room is not None and room.representative_agent_id == target.agent_id:
            room.representative_agent_id = None

    # 6. Capture a snapshot for the broadcast BEFORE the delete — we
    #    still want ``user_id`` (or empty string for agent removals)
    #    in the outgoing frame, and we need the list of remaining
    #    subscribers who aren't the departed participant.
    removed_user_id = target.user_id or ""

    # 7. Find remaining subscribers (all OTHER participants in the
    #    room). We deliberately exclude ``target.id`` so we don't send
    #    to the socket that's about to be disconnected anyway — and
    #    so we match the "existing members" semantics used by the
    #    ``added`` broadcast in auth/routes.py and add_participant.
    other_pids = (
        await db.execute(
            select(Participant.id).where(
                Participant.room_id == room_id,
                Participant.id != target.id,
            )
        )
    ).scalars().all()

    # 8. Delete the row and commit.
    await db.execute(delete(Participant).where(Participant.id == target.id))
    await db.commit()

    # 9. Broadcast ``RoomMembershipChangedOut`` to remaining subscribers.
    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        from doorae.ws.protocol import RoomMembershipChangedOut

        frame = RoomMembershipChangedOut(
            action="removed",
            room_id=room_id,
            user_id=removed_user_id,
        )
        for pid in other_pids:
            await manager.send_to(pid, frame)

    return None


class RoomUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    # #148 — ``None`` means "don't touch" so a rename PATCH can't
    # accidentally reset the ambient-sharing flag. Explicit ``True``/
    # ``False`` toggles it.
    context_window_enabled: bool | None = None


@router.patch("/{room_id}", response_model=RoomOut)
async def update_room(
    room_id: str,
    body: RoomUpdate,
    # Room metadata changes are closed to guests (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Update room name, description, and/or context-window flag."""
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if body.name is not None:
        room.name = body.name
    if body.description is not None:
        room.description = body.description
    if body.context_window_enabled is not None:
        room.context_window_enabled = body.context_window_enabled
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
    request: Request,
    # ``forbid_guest`` is the dep gate; the per-room admin/owner
    # check happens below before any DB write so a non-member
    # outsider gets 403, not 404 (no room-existence oracle).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Delete a room, cascading: archive child rooms.

    Authorisation: global admin OR a room-level ``admin``/``owner``
    Participant. Same rule as invite issuance and participant
    removal — see ``api/v1/invites.py::_require_room_admin_or_owner``.
    Anyone else (rank-and-file member, outsider, agent, guest) is
    rejected with 403.

    On success we broadcast ``RoomDeletedOut`` so any user with a
    live WS in this room (or any of its participants reached via a
    sibling-room WS) can remove the room from their tree without
    waiting for a polled refetch.
    """
    # Lazy import keeps the module dependency graph flat — same
    # pattern the membership-change broadcast follows in
    # ``add_participant``.
    from doorae.api.v1.invites import _require_room_admin_or_owner

    await _require_room_admin_or_owner(room_id, identity, db)

    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    # Capture the audience BEFORE we delete the participant rows. We
    # need:
    # - participant_ids in the room being deleted, so we can broadcast
    #   over those still-open WS connections,
    # - user_ids of those participants, so we can also reach each
    #   user's OTHER active WS (e.g. a sidebar mounted in a sibling
    #   room) — the ``add_participant`` push pattern from #19.
    parts = (
        await db.execute(
            select(Participant).where(Participant.room_id == room_id)
        )
    ).scalars().all()
    user_ids = {p.user_id for p in parts if p.user_id}

    await archive_child_rooms(db, room_id)
    await db.execute(delete(Participant).where(Participant.room_id == room_id))
    await db.delete(room)
    await db.commit()

    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        from doorae.ws.protocol import RoomDeletedOut

        frame = RoomDeletedOut(room_id=room_id)
        # 1) Anyone subscribed to the deleted room's WS at this
        #    instant gets the news directly. ``broadcast`` is best
        #    effort and tolerant of already-closed sockets.
        await manager.broadcast(room_id, frame)

        # 2) Members who were NOT subscribed to the deleted room (e.g.
        #    they were viewing a sibling room) need a poke too so
        #    their sidebar refetches and the stale entry disappears.
        #    Look up each user's other active participant_ids and
        #    push the same frame.
        if user_ids:
            other_pids = (
                await db.execute(
                    select(Participant.id).where(
                        Participant.user_id.in_(user_ids)
                    )
                )
            ).scalars().all()
            for pid in other_pids:
                await manager.send_to(pid, frame)

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
    child, agent_ids = await create_sub_room(
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

    # Push ``JoinRoomOut`` directly to each participating agent's
    # *other* WS sessions via ``ensure_agent_in_room``. The old
    # implementation broadcast over the parent room, which silently
    # dropped the frame for any agent that hadn't subscribed to the
    # parent — a surprisingly common case. Reusing the helper keeps
    # this path on the same membership invariant as ``#room``
    # auto-join and ``add_participant`` (issue #50).
    manager = getattr(request.app.state, "connection_manager", None)
    for agent_id in agent_ids:
        await ensure_agent_in_room(
            db,
            manager,
            room_id=child.id,
            agent_id=agent_id,
        )

    return child


async def _broadcast_pin_order_to_user(
    request: Request,
    *,
    user_id: str,
    pinned_room_ids: list[str],
    db: AsyncSession,
) -> None:
    """Fan out a pin-order change frame to every live WS session of ``user_id``.

    The ``ConnectionManager`` is keyed by ``participant_id`` rather
    than ``user_id``, so we resolve the user's participant rows here
    and call ``send_to`` per id. Silent when no manager is attached
    (e.g. unit tests without a running WS dispatcher).
    """
    manager = getattr(request.app.state, "connection_manager", None)
    if manager is None:
        return
    result = await db.execute(
        select(Participant.id).where(Participant.user_id == user_id)
    )
    participant_ids = [row[0] for row in result.all()]
    if not participant_ids:
        return
    from doorae.ws.protocol import RoomPinOrderChangedOut
    frame = RoomPinOrderChangedOut(
        user_id=user_id, pinned_room_ids=pinned_room_ids
    )
    for pid in participant_ids:
        await manager.send_to(pid, frame)


# -- Sidebar pin / reorder ----------------------------------------------------
#
# Two endpoints cover the drag-and-drop reorder flow (#47):
#
# - ``PATCH /{room_id}/pin`` flips pin on/off for the caller. Pin on
#   places the room at the tail of the pinned section. Pin off clears
#   ``sort_order`` so the room rejoins the default alphabetical list.
# - ``PUT /pin-order`` overwrites the caller's pinned section order
#   in a single idempotent call. The frontend sends a full snapshot
#   after each drop so replays and late retries stay safe.
#
# Both are guest-forbidden: a guest session is bound to a single
# room, so sidebar pinning carries no meaning. Agent-only rooms are
# rejected by the service layer because pin state is stored on a
# ``Participant`` row keyed by ``user_id``.


@router.patch("/{room_id}/pin", response_model=PinOrderOut)
async def toggle_room_pin(
    room_id: str,
    body: PinRoomBody,
    request: Request,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Toggle sidebar pin for the caller's participation in ``room_id``."""
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="Only users can pin rooms")
    pinned_ids = await set_room_pinned(
        db, user_id=identity.id, room_id=room_id, pinned=body.pinned
    )
    await db.commit()
    # Broadcast to caller's other sessions so multi-tab stays in sync.
    await _broadcast_pin_order_to_user(
        request, user_id=identity.id, pinned_room_ids=pinned_ids, db=db
    )
    return PinOrderOut(pinned_room_ids=pinned_ids)


@router.put("/pin-order", response_model=PinOrderOut)
async def set_pin_order(
    body: PinOrderBody,
    request: Request,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Rewrite the caller's pinned-section order from a full snapshot."""
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="Only users can reorder pinned rooms")
    pinned_ids = await reorder_pinned_rooms(
        db, user_id=identity.id, room_ids=body.room_ids
    )
    await db.commit()
    await _broadcast_pin_order_to_user(
        request, user_id=identity.id, pinned_room_ids=pinned_ids, db=db
    )
    return PinOrderOut(pinned_room_ids=pinned_ids)
