"""REST endpoints for Room CRUD — ``/api/v1/rooms``."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from doorae.api.v1.invites import _require_room_admin_or_owner
from doorae.auth.dependencies import Identity, GuestClaims
from doorae.db.models import (
    Agent,
    Participant,
    Room,
    RoomArtifact,
    RoomSharedFile,
    User,
)
from doorae.dependencies import (
    forbid_guest,
    get_admin_identity,
    get_current_identity,
    get_db,
)
from doorae.rooms import (
    artifacts as artifacts_service,
    shared_files as shared_files_service,
)
from doorae.rooms import artifact_storage
from doorae.rooms.file_storage import FileTooLargeError
from doorae.rooms.membership import add_user_to_room, ensure_agent_in_room
from doorae.rooms.service import (
    archive_child_rooms,
    create_sub_room,
    reorder_pinned_rooms,
    set_room_pinned,
)
from doorae.rooms.shared_files import (
    InvalidFilenameError,
    UnsupportedMimeError,
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
    # #179 — DM rooms live outside any project (``project_id=NULL``) so
    # they cannot be cascade-deleted alongside an arbitrary project.
    # Regular rooms always carry a project id; this is only None for DMs.
    project_id: Optional[str] = None
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
    # wires the broadcast-side logic. #225 flipped the default to True
    # (migration 028); the PATCH field is admin-only.
    context_window_enabled: bool = True
    # #159 Phase A — room-scoped speaker strategy. ``mentioned_only``
    # (default) is the pre-#159 behaviour; ``round_robin`` rotates
    # across agents; ``orchestrator`` delegates next-speaker choice to
    # ``orchestrator_agent_id`` via the ``handoff_to`` tool call. The
    # orchestrator pointer is a separate column from
    # ``representative_agent_id`` so cross-room and in-room roles stay
    # legible (decisions §3.2 A).
    speaker_strategy: str = "mentioned_only"
    orchestrator_agent_id: Optional[str] = None
    # #237 — when True the WS welcome frame carries ``ephemeral=True``
    # so the agent's system_prompt gets a "do not write to memory/notes.md"
    # directive. Trust-model signal, not a hard FS guard (see plan §3.2).
    # Default False keeps legacy rooms behaving as before.
    ephemeral: bool = False
    # #266 — opt-in toggle for surfacing human participants in the
    # task assignee dropdown. Default False (agent-only assignment is
    # the primary mode). Surfaced here so the frontend can hide the
    # human group without a second round-trip.
    allow_human_assignment: bool = False
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
    representative_agent_id: Optional[str] = None,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """List rooms, optionally filtered by project and/or DM status.

    For guests this returns at most the single room their JWT is
    bound to (§11.5 "다른 룸 조회 ❌"). Registered users still see
    the full tree the endpoint used to return — project-/dm-level
    filtering is unchanged.

    #237 — ``representative_agent_id`` filters to rooms that belong
    to a specific agent. Combined with ``is_dm=true`` this returns the
    caller's full DM list for that agent (sidebar multi-DM view).
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
    if representative_agent_id is not None:
        query = query.where(Room.representative_agent_id == representative_agent_id)
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
                speaker_strategy=r.speaker_strategy,
                orchestrator_agent_id=r.orchestrator_agent_id,
                ephemeral=r.ephemeral,
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
        speaker_strategy=room.speaker_strategy,
        orchestrator_agent_id=room.orchestrator_agent_id,
        ephemeral=room.ephemeral,
        participants=participant_outs,
    )


@router.post("/{room_id}/participants", status_code=201, response_model=ParticipantOut)
async def add_participant(
    room_id: str,
    body: ParticipantAdd,
    request: Request,
    background: BackgroundTasks,
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
        participant, created = await ensure_agent_in_room(
            db,
            manager,
            room_id=room_id,
            agent_id=body.agent_id,
            role=body.role,
        )
        # #246 — a freshly-joined agent needs the room's existing
        # shared files materialised on its machine. Skip when the
        # participant already existed so replays / duplicate joins
        # don't re-emit the whole fan-out.
        if created:
            _schedule_shared_files_backfill(
                request, background, room_id=room_id, agent_id=body.agent_id
            )
        # #227 — ``ensure_agent_in_room`` delivers a JoinRoomOut best-
        # effort. When the agent's WS is not subscribed to the pid we
        # target the frame drops silently and the agent stays offline
        # in this new room forever. Hand off to the lifecycle so the
        # machine receives an authoritative ``sync_desired_state`` with
        # the refreshed rooms list (``bump_generation`` for running
        # agents, ``request_start`` for dormant ones). Missing
        # ``agent_lifecycle`` on app.state falls back to the pre-#227
        # best-effort-only behaviour for tests that don't wire the
        # scheduler.
        lifecycle = getattr(request.app.state, "agent_lifecycle", None)
        if lifecycle is not None:
            await lifecycle.on_room_added(body.agent_id)
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
    background: BackgroundTasks,
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

    # Capture the removed agent's id before deletion so we can tell
    # its machine to drop the room's shared files from
    # ``memory/shared/`` (#246). The agent remains a participant in
    # *other* rooms, so we only want a targeted delete — not the
    # global ``fan_out_delete`` that blasts every participant.
    removed_agent_id = target.agent_id

    # 8. Delete the row and commit.
    await db.execute(delete(Participant).where(Participant.id == target.id))
    await db.commit()

    if removed_agent_id is not None:
        _schedule_shared_files_delete_for_agent(
            request,
            background,
            room_id=room_id,
            agent_id=removed_agent_id,
        )

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
    # ``False`` toggles it. #225 promoted this to an admin-only field
    # (enforced in the handler below alongside the #159 Phase C fields)
    # because flipping it affects token cost for the entire room.
    context_window_enabled: bool | None = None
    # #159 Phase C — room-scoped speaker strategy. Admin-only: the
    # handler rejects non-admin callers when either of these fields
    # is present so a member rename PATCH still works. ``None`` means
    # "don't touch" following the context_window pattern above.
    speaker_strategy: str | None = None
    orchestrator_agent_id: str | None = None
    # #237 — ephemeral toggle. For DM rooms any member (the DM owner)
    # can flip this; for non-DM rooms admin required. ``None`` means
    # "don't touch" following the context_window pattern above.
    ephemeral: bool | None = None


# Strategy names accepted by the dispatcher in
# ``doorae_agent.integrations.base.decide_policy``. The ``bidding``
# and ``llm_judge`` values listed in plan-159 §1 are intentionally
# excluded here — they're future work with uncertain cost profiles.
_VALID_SPEAKER_STRATEGIES: frozenset[str] = frozenset(
    {"mentioned_only", "round_robin", "orchestrator"}
)


@router.patch("/{room_id}", response_model=RoomOut)
async def update_room(
    room_id: str,
    body: RoomUpdate,
    request: Request,
    # Room metadata changes are closed to guests (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Update room metadata.

    Fields split by permission tier:

    - Open to every member: ``name``, ``description``.
    - Admin-only (#159 Phase C, #225): ``speaker_strategy``,
      ``orchestrator_agent_id``, ``context_window_enabled``.
      Touching any of these requires ``identity.is_admin`` — mirrors
      the DESIGN.md guidance that dispatch-mode controls stay on the
      admin surface because a mistaken flip silently reroutes who
      replies or balloons token cost.

    The admin gate is enforced inside the handler (not via
    ``get_admin_identity``) so a member PATCH that only renames the
    room stays open. Sending an admin-only field as a non-admin
    returns ``403`` with no partial write.
    """
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    admin_only_fields_present = (
        body.speaker_strategy is not None
        or body.orchestrator_agent_id is not None
        or body.context_window_enabled is not None
    )
    if admin_only_fields_present:
        # Mirror ``get_admin_identity``'s shape but as an inline gate —
        # using the dep directly would reject every non-admin rename
        # too, and we want the rename surface to stay open.
        is_admin = (
            identity.kind == "user"
            and identity.claims is not None
            and getattr(identity.claims, "is_admin", False)
        )
        if not is_admin:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Admin required to change speaker_strategy, "
                    "orchestrator_agent_id, or context_window_enabled"
                ),
            )

    # #237 — ephemeral toggle: DM owner can toggle their own DM;
    # admin can toggle any room. "DM owner" = user participant of the
    # DM room. Non-DM rooms fall back to admin-only to match the
    # ``context_window_enabled`` trust tier.
    if body.ephemeral is not None:
        is_admin = (
            identity.kind == "user"
            and identity.claims is not None
            and getattr(identity.claims, "is_admin", False)
        )
        if not is_admin:
            if not room.is_dm:
                raise HTTPException(
                    status_code=403,
                    detail="Admin required to toggle ephemeral on non-DM rooms",
                )
            # DM room: caller must be a participant (owner or member).
            if identity.kind != "user":
                raise HTTPException(
                    status_code=403,
                    detail="Only room members can toggle ephemeral on a DM",
                )
            part_stmt = select(Participant).where(
                Participant.room_id == room_id,
                Participant.user_id == identity.id,
            )
            part = (await db.execute(part_stmt)).scalar_one_or_none()
            if part is None:
                raise HTTPException(
                    status_code=403,
                    detail="Only room members can toggle ephemeral on a DM",
                )

    if body.speaker_strategy is not None:
        if body.speaker_strategy not in _VALID_SPEAKER_STRATEGIES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unknown speaker_strategy — expected one of "
                    + ", ".join(sorted(_VALID_SPEAKER_STRATEGIES))
                ),
            )

    # Validate orchestrator agent membership up front — matches the
    # ``set_representative`` contract so an admin can't point
    # ``orchestrator_agent_id`` at an agent that isn't actually in
    # the room. A ``None`` payload clears the pointer (strategy can
    # fall back to mentioned_only behaviour downstream).
    if body.orchestrator_agent_id is not None:
        stmt = select(Participant).where(
            Participant.room_id == room_id,
            Participant.agent_id == body.orchestrator_agent_id,
        )
        part = (await db.execute(stmt)).scalar_one_or_none()
        if part is None:
            raise HTTPException(
                status_code=400,
                detail="Agent is not a participant of this room",
            )

    if body.name is not None:
        room.name = body.name
    if body.description is not None:
        room.description = body.description
    if body.context_window_enabled is not None:
        room.context_window_enabled = body.context_window_enabled
    if body.speaker_strategy is not None:
        room.speaker_strategy = body.speaker_strategy
    if body.orchestrator_agent_id is not None:
        room.orchestrator_agent_id = body.orchestrator_agent_id
    if body.ephemeral is not None:
        room.ephemeral = body.ephemeral
    await db.commit()
    await db.refresh(room)

    # Issue #221 — broadcast settings changes to subscribed clients so
    # connected agents refresh their cached dispatch mode without a
    # reconnect. ``None`` fields mean "not touched by this PATCH" so a
    # rename-only edit doesn't reset other caches on the receiving end.
    # Skipped entirely for rename-only PATCHes to keep the wire quiet
    # when no cached state depends on the change.
    settings_touched = (
        body.speaker_strategy is not None
        or body.orchestrator_agent_id is not None
        or body.context_window_enabled is not None
        or body.ephemeral is not None
    )
    if settings_touched:
        manager = getattr(request.app.state, "connection_manager", None)
        if manager is not None:
            from doorae.ws.protocol import RoomSettingsChangedOut

            frame = RoomSettingsChangedOut(
                room_id=room_id,
                speaker_strategy=body.speaker_strategy,
                orchestrator_agent_id=body.orchestrator_agent_id,
                context_window_enabled=body.context_window_enabled,
                ephemeral=body.ephemeral,
            )
            await manager.broadcast(room_id, frame)
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


# ---------------------------------------------------------------------------
# Token telemetry (#157 Phase C)
# ---------------------------------------------------------------------------


@router.get("/{room_id}/token-stats")
async def get_room_token_stats(
    room_id: str,
    # Admin-only — the endpoint leaks per-participant volumes that a
    # regular member shouldn't see.
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return rolling-window token estimates for a room.

    Response shape (see ``doorae.rooms.token_stats`` for details)::

        {
          "window_1h": {
            "tokens": int, "messages": int, "agents": int,
            "per_agent": [
              {"participant_id", "agent_name", "tokens", "messages",
               "last_active_at"},
              ...
            ],
          },
          "window_24h": { /* same */ },
        }

    Token counts are conservative estimates (``len(content) // 4``) —
    accurate per-engine tokenisers are future work. ``per_agent``
    lets #159 Phase D render the per-agent usage panel without
    repeating the aggregation client-side.
    """
    from doorae.rooms.token_stats import (
        get_room_token_stats as _compute,
        serialise_window,
    )

    # Ensure the room exists (a 404 reads better than an empty stats
    # payload, and matches the rest of this router's conventions).
    room = await db.scalar(select(Room).where(Room.id == room_id))
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    stats = await _compute(db, room_id)
    return {
        label: serialise_window(window)
        for label, window in stats.items()
    }


# ---------------------------------------------------------------------------
# Room shared files (#246)
# ---------------------------------------------------------------------------


class RoomSharedFileOut(BaseModel):
    id: str
    room_id: str
    filename: str
    storage_name: str
    sha256: str
    size_bytes: int
    mime: str
    uploaded_by: Optional[str]
    created_at: datetime

    @classmethod
    def from_row(cls, row: RoomSharedFile) -> "RoomSharedFileOut":
        return cls(
            id=row.id,
            room_id=row.room_id,
            filename=row.filename,
            storage_name=row.storage_name,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            mime=row.mime,
            uploaded_by=row.uploaded_by,
            created_at=row.created_at,
        )


def _schedule_shared_files_backfill(
    request: Request,
    background: BackgroundTasks,
    *,
    room_id: str,
    agent_id: str,
) -> None:
    """Queue a ``backfill_agent`` fan-out on the BackgroundTasks stack.

    Uses a short-lived session spawned from the app's session factory
    so we don't hold the request's DB session open across the
    fan-out — the opposite would serialise subsequent requests on
    this transaction.
    """
    session_factory = getattr(request.app.state, "session_factory", None)
    machine_bus = getattr(request.app.state, "machine_bus", None)
    config = getattr(request.app.state, "config", None)
    if session_factory is None or machine_bus is None or config is None:
        return

    async def _run() -> None:
        async with session_factory() as bg_session:
            await shared_files_service.backfill_agent(
                bg_session,
                machine_bus=machine_bus,
                room_files_dir=config.room_files_dir,
                room_id=room_id,
                agent_id=agent_id,
            )

    background.add_task(_run)


def _schedule_shared_files_delete_for_agent(
    request: Request,
    background: BackgroundTasks,
    *,
    room_id: str,
    agent_id: str,
) -> None:
    """Queue delete frames for every shared file in the room, targeted
    at a single agent that's leaving the room.

    Uses a dedicated session and the ``fan_out_delete`` helper's
    machine-bus wrapper, but scoped to one agent_id so we don't nuke
    other participants' copies of the file. The service's generic
    ``fan_out_delete`` fans to every placed agent in the room, which
    is the right behaviour for file-wide deletes but wrong for
    "this one agent is leaving".
    """
    session_factory = getattr(request.app.state, "session_factory", None)
    machine_bus = getattr(request.app.state, "machine_bus", None)
    config = getattr(request.app.state, "config", None)
    if session_factory is None or machine_bus is None or config is None:
        return

    async def _run() -> None:
        async with session_factory() as bg_session:
            agent = await bg_session.get(Agent, agent_id)
            if agent is None or agent.placed_on_machine_id is None:
                return
            files = await shared_files_service.list_shared_files(
                bg_session, room_id=room_id
            )
            for file in files:
                await machine_bus.send(
                    agent.placed_on_machine_id,
                    {
                        "type": "agent_memory_shared_file_delete",
                        "agent_id": agent_id,
                        "storage_name": file.storage_name,
                    },
                )

    background.add_task(_run)


async def _require_room_participant(
    room_id: str, identity: Identity, db: AsyncSession
) -> None:
    """Raise 403 when ``identity`` is not a participant of ``room_id``.

    Global admins pass unconditionally. Guests are always rejected —
    the shared-file feature isn't scoped to guest sessions.
    """
    if identity.kind == "guest":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        )
    if (
        identity.kind == "user"
        and identity.claims is not None
        and getattr(identity.claims, "is_admin", False)
    ):
        return

    stmt = select(Participant).where(Participant.room_id == room_id)
    if identity.kind == "user":
        stmt = stmt.where(Participant.user_id == identity.id)
    elif identity.kind == "agent":
        stmt = stmt.where(Participant.agent_id == identity.id)
    else:  # pragma: no cover — unknown identity kind
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a participant of this room",
        )


@router.post(
    "/{room_id}/files",
    status_code=status.HTTP_201_CREATED,
    response_model=RoomSharedFileOut,
)
async def upload_room_shared_file(
    room_id: str,
    request: Request,
    background: BackgroundTasks,
    upload: UploadFile = File(...),
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
) -> RoomSharedFileOut:
    """Attach a file to the room. The bytes land on the server's disk
    and are copy-distributed to every participating agent's
    ``memory/shared/`` directory via a background fan-out (#246).

    Same filename re-uploaded = upsert: the file's bytes are replaced
    atomically and a fresh write-frame goes to every agent.
    """
    await _require_room_participant(room_id, identity, db)

    room = await db.scalar(select(Room).where(Room.id == room_id))
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    uploader_user_id = identity.id if identity.kind == "user" else None

    config = request.app.state.config
    machine_bus = request.app.state.machine_bus

    try:
        row = await shared_files_service.upload_file(
            db,
            room_files_dir=config.room_files_dir,
            room_id=room_id,
            uploader_user_id=uploader_user_id,
            filename=upload.filename or "upload",
            mime=upload.content_type or "application/octet-stream",
            stream=upload.file,
        )
    except UnsupportedMimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        )
    except InvalidFilenameError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        )

    # Response snapshot before scheduling the fan-out so the caller's
    # payload is stable even if a later ORM refresh races with
    # background work on the same row.
    response = RoomSharedFileOut.from_row(row)

    file_id = row.id

    async def _fan_out() -> None:
        # Re-open the row in a short-lived session so we don't hold
        # the request's session open across the fan-out (which would
        # serialise every subsequent request on this transaction).
        session_factory = request.app.state.session_factory
        async with session_factory() as bg_session:
            fresh = await bg_session.get(RoomSharedFile, file_id)
            if fresh is None:
                return
            await shared_files_service.fan_out_write(
                bg_session,
                machine_bus=machine_bus,
                room_files_dir=config.room_files_dir,
                file=fresh,
            )

    background.add_task(_fan_out)
    return response


@router.get(
    "/{room_id}/files", response_model=list[RoomSharedFileOut]
)
async def list_room_shared_files(
    room_id: str,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
) -> list[RoomSharedFileOut]:
    """List shared files attached to the room. Participants only."""
    await _require_room_participant(room_id, identity, db)
    rows = await shared_files_service.list_shared_files(db, room_id=room_id)
    return [RoomSharedFileOut.from_row(r) for r in rows]


@router.delete(
    "/{room_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_room_shared_file(
    room_id: str,
    file_id: str,
    request: Request,
    background: BackgroundTasks,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a shared file from the room. Participants only.

    The DB row and on-disk bytes are removed synchronously; a delete
    fan-out to participating agents is scheduled in the background so
    their ``memory/shared/<storage_name>`` copies are pruned too.
    """
    await _require_room_participant(room_id, identity, db)

    config = request.app.state.config
    machine_bus = request.app.state.machine_bus

    removed = await shared_files_service.delete_shared_file(
        db, room_files_dir=config.room_files_dir, file_id=file_id
    )
    if removed is None:
        raise HTTPException(status_code=404, detail="File not found")
    if removed.room_id != room_id:
        # Rare — the ``file_id`` belongs to a different room. Surface
        # as 404 rather than leaking the cross-room existence.
        raise HTTPException(status_code=404, detail="File not found")

    storage_name = removed.storage_name

    async def _fan_out_delete() -> None:
        session_factory = request.app.state.session_factory
        async with session_factory() as bg_session:
            await shared_files_service.fan_out_delete(
                bg_session,
                machine_bus=machine_bus,
                room_id=room_id,
                storage_name=storage_name,
            )

    background.add_task(_fan_out_delete)


# ---------------------------------------------------------------------------
# Room artifacts (#290 Phase B) — agent-produced files, surfaced in the
# right-hand sidebar panel. Read/delete only here; ingestion happens
# over the WebSocket via ``room_artifact_produced`` frames.
# ---------------------------------------------------------------------------


class RoomArtifactOut(BaseModel):
    id: str
    room_id: str
    produced_by_agent_id: Optional[str]
    filename: str
    sha256: str
    size_bytes: int
    mime: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: RoomArtifact) -> "RoomArtifactOut":
        return cls(
            id=row.id,
            room_id=row.room_id,
            produced_by_agent_id=row.produced_by_agent_id,
            filename=row.filename,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            mime=row.mime,
            created_at=row.created_at,
        )


@router.get(
    "/{room_id}/artifacts", response_model=list[RoomArtifactOut]
)
async def list_room_artifacts(
    room_id: str,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
) -> list[RoomArtifactOut]:
    """List artifacts produced into the room. Participants only."""
    await _require_room_participant(room_id, identity, db)
    rows = await artifacts_service.list_artifacts(db, room_id=room_id)
    return [RoomArtifactOut.from_row(r) for r in rows]


@router.get("/{room_id}/artifacts/{artifact_id}")
async def download_room_artifact(
    room_id: str,
    artifact_id: str,
    request: Request,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Stream the artifact bytes back to the requester. Participants
    only. ``Content-Disposition`` is set so browsers offer a sensible
    save-as dialog while still rendering inline previews when the MIME
    permits (image/*, text/*).
    """
    from fastapi.responses import Response

    await _require_room_participant(room_id, identity, db)
    row = await artifacts_service.get_artifact(
        db, room_id=room_id, artifact_id=artifact_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    config = request.app.state.config
    try:
        body = await asyncio.to_thread(
            artifact_storage.read_bytes,
            config.artifact_files_dir,
            row.storage_path,
        )
    except FileNotFoundError:
        # DB row exists but the disk file is gone — surface as 404
        # so clients can refresh; log the integrity gap on the server.
        raise HTTPException(status_code=404, detail="Artifact bytes missing")

    # ``inline`` lets <img src="..."> work directly from the URL while
    # still suggesting a filename for explicit Save As.
    safe_name = row.filename.replace('"', "")
    return Response(
        content=body,
        media_type=row.mime,
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Content-Length": str(row.size_bytes),
        },
    )


@router.delete(
    "/{room_id}/artifacts/{artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_room_artifact(
    room_id: str,
    artifact_id: str,
    request: Request,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove an artifact (row + disk blob). Participants only.

    Broadcasts ``room_artifact.removed`` so other subscribers refresh
    their panel without having to poll.
    """
    await _require_room_participant(room_id, identity, db)
    config = request.app.state.config
    ok = await artifacts_service.delete_artifact(
        db,
        artifact_files_dir=config.artifact_files_dir,
        room_id=room_id,
        artifact_id=artifact_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Artifact not found")

    connection_manager = getattr(request.app.state, "connection_manager", None)
    if connection_manager is not None:
        from doorae.ws.protocol import RoomArtifactRemovedOut

        await connection_manager.broadcast(
            room_id,
            RoomArtifactRemovedOut(room_id=room_id, artifact_id=artifact_id),
        )
