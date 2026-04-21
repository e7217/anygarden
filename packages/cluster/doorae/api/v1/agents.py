"""REST endpoints for Agent lifecycle — ``/api/v1/agents``."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.agent_files import AgentFilePathError, validate_agent_file_path
from doorae.auth.dependencies import Identity
from doorae.db.models import ActivityLog, Agent, AgentFile, AgentToken, Machine, MachineEngine, Participant, Room
from doorae.dependencies import get_admin_identity, get_db
from doorae.engines import get_engine_entry
from doorae.rooms.membership import ensure_agent_in_room

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# ── Request / Response schemas ───────────────────────────────────────


class AgentCreate(BaseModel):
    engine: str
    name: str
    rooms: list[str] = []
    profile_yaml: Optional[str] = None
    # Phase 0 file-manifest fields. Both optional — a caller that
    # only wants "simple" agents can still pass just name + engine.
    # When provided here, they are written to the DB during create;
    # the materializer picks them up on the next spawn frame so the
    # engine's native file discovery finds AGENTS.md + skills/ on
    # disk exactly as shipped from the server.
    agents_md: Optional[str] = None
    files: Optional[dict[str, str]] = None
    reasoning_effort: Optional[str] = None
    model: Optional[str] = None
    restart_policy: str = "restart_anywhere"
    # Issue #73 — runtime selector (``python`` default, ``typescript``
    # for the new doorae-agent-ts path). Accepting it here lets admins
    # pin a specific runtime when they know the engine has a TS-native
    # SDK they want to exercise (e.g. Claude Code v2).
    runtime: str = "python"


class AgentUpdate(BaseModel):
    """Fields that can be updated on an existing agent.

    All fields optional — ``PUT`` here means "update whichever
    fields the caller sent". ``agents_md`` can be set to ``None``
    explicitly to clear the role/rules body; to avoid confusing
    "null means no change" vs "null means clear", we use a separate
    flag on the model: ``agents_md_set`` must be ``True`` for
    ``agents_md`` to be applied, even if its value is ``None``.

    Keeping the update surface narrow intentionally — engine change
    and restart_policy change are not supported here because both
    affect a running agent's process model and deserve explicit
    lifecycle events rather than a silent PUT.
    """

    name: Optional[str] = None
    agents_md: Optional[str] = None
    agents_md_set: bool = False
    reasoning_effort: Optional[str] = None
    reasoning_effort_set: bool = False
    model: Optional[str] = None
    model_set: bool = False
    # Issue #73 — runtime is editable post-creation. A real change
    # requires a restart (bump_generation → machine respawns with
    # the new runtime) which ``update_agent`` already triggers.
    runtime: Optional[str] = None
    runtime_set: bool = False
    # Issue #101 — avatar kind/value. Pure UI metadata; the PATCH
    # handler skips ``bump_generation`` when these are the only
    # fields that changed, so an admin reshuffling avatars never
    # triggers spurious agent restarts. ``avatar_kind`` is
    # ``'emoji'``, ``'lucide'``, or ``None`` (reset to initials).
    avatar_kind: Optional[str] = None
    avatar_kind_set: bool = False
    avatar_value: Optional[str] = None
    avatar_value_set: bool = False
    # Issue #148 Part 2 — agent-side opt-out from ambient context
    # window. ``_set`` flag mirrors the established pattern so a
    # rename PATCH can't silently reset the flag back to False.
    context_window_opt_out: Optional[bool] = None
    context_window_opt_out_set: bool = False


class AgentOut(BaseModel):
    id: str
    name: str
    engine: str
    desired_state: str
    actual_state: str
    placed_on_machine_id: Optional[str] = None
    restart_policy: str
    agents_md: Optional[str] = None
    # Last failure reason as recorded by the lifecycle — surfaced
    # to the admin UI so a ``pending`` or ``crashed`` agent shows
    # *why* on hover instead of being silently stuck. Populated
    # by ``AgentLifecycle`` on crash or refused dispatch (e.g.
    # ``spawn_refused_no_rooms``); None for agents that never
    # failed.
    reasoning_effort: Optional[str] = None
    model: Optional[str] = None
    # Issue #73 — exposed read-only so the admin UI can render a
    # badge next to the engine picker without re-querying.
    runtime: str = "python"
    last_crash_reason: Optional[str] = None
    # Issue #101 — admin-chosen avatar override. Both NULL means
    # the UI falls back to the seed-driven initial.
    avatar_kind: Optional[str] = None
    avatar_value: Optional[str] = None
    # Issue #148 Part 2 — mirrors the new DB flag so the admin UI
    # can render the opt-out toggle without a second query. Part 3
    # will wire this into the spawn path so agents actually honour
    # the flag at runtime.
    context_window_opt_out: bool = False
    model_config = {"from_attributes": True, "protected_namespaces": ()}


class AgentFileOut(BaseModel):
    """Row from ``agent_files`` visible over the REST API."""

    path: str
    content: str
    updated_at: datetime
    model_config = {"from_attributes": True}


class AgentFileUpsert(BaseModel):
    """Body for ``PUT /agents/{id}/files`` — create-or-update."""

    path: str
    content: str


class AgentFileDelete(BaseModel):
    """Body for ``DELETE /agents/{id}/files`` — single-file delete.

    Using a request body (rather than a query string) because the
    path whitelist allows slashes (``skills/greeting/SKILL.md``) and
    percent-encoding those in every client is ergonomically painful.
    """

    path: str


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AgentOut)
async def create_agent(
    body: AgentCreate,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Declarative agent creation — schedules start on a suitable machine.

    Optional ``agents_md`` + ``files`` are stored alongside the agent
    row and shipped with every subsequent ``spawn_agent`` frame so
    the machine-side materializer lays them on disk. Validate every
    file path BEFORE writing anything so a single bad path fails
    the whole request cleanly (no half-materialized rows).
    """
    # Reject invalid file paths up-front. Defense-in-depth: the
    # machine materializer also validates, but catching here lets
    # us return 400 with a clear reason instead of crashing the
    # spawn request later.
    if body.files:
        for path in body.files:
            try:
                validate_agent_file_path(path)
            except AgentFilePathError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid file path {path!r}: {exc}",
                )

    agent = Agent(
        name=body.name,
        engine=body.engine,
        desired_state="running",
        actual_state="pending",
        profile_yaml=body.profile_yaml,
        agents_md=body.agents_md,
        reasoning_effort=body.reasoning_effort,
        model=body.model,
        restart_policy=body.restart_policy,
        runtime=body.runtime,
    )
    db.add(agent)
    await db.flush()

    # Seed AgentFile rows from the request manifest.
    if body.files:
        for path, content in body.files.items():
            db.add(AgentFile(agent_id=agent.id, path=path, content=content))

    # Add agent as participant to requested rooms
    for room_id in body.rooms:
        db.add(Participant(room_id=room_id, agent_id=agent.id, role="member"))

    # Auto-create a DM room so the agent always has at least one
    # room and can be started immediately. #179 — DMs live outside
    # any project (``project_id=NULL``) so a project deletion can
    # never cascade-kill an agent's DM. Project membership was
    # always arbitrary for DMs (the old "first_project" heuristic
    # had no domain meaning), and decoupling removes the data-loss
    # foot-gun for admins.
    dm_room = Room(
        project_id=None,
        name=f"DM: {agent.name}",
        is_dm=True,
    )
    db.add(dm_room)
    await db.flush()
    db.add(Participant(room_id=dm_room.id, user_id=identity.id, role="owner"))
    db.add(Participant(room_id=dm_room.id, agent_id=agent.id, role="member"))

    await db.commit()
    await db.refresh(agent)

    # Agent always has at least the DM room → start immediately.
    lifecycle = request.app.state.agent_lifecycle
    await lifecycle.request_start(agent.id)
    await db.refresh(agent)

    return agent


@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Update mutable scalar fields on an agent.

    Currently supported: ``name``, ``agents_md``, ``reasoning_effort``,
    ``model``. When config fields change the agent's generation is
    bumped so the machine knows to restart with the new config.
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Two change counters so avatar-only edits can skip the
    # ``bump_generation`` call: avatars are UI metadata and do not
    # flow into the machine-side materializer, so restarting the
    # subprocess for an emoji swap would be surprising. Any other
    # mutable field flips ``non_avatar_changed`` and keeps the
    # existing "mutate → generation bump → respawn" semantics.
    non_avatar_changed = False
    avatar_changed = False
    if body.name is not None:
        agent.name = body.name
        non_avatar_changed = True
    if body.agents_md_set:
        # Explicit opt-in flag is needed to distinguish "omit the
        # field" (no change) from "set the field to null" (clear
        # the role/rules body).
        agent.agents_md = body.agents_md
        non_avatar_changed = True
    if body.reasoning_effort_set:
        agent.reasoning_effort = body.reasoning_effort
        non_avatar_changed = True
    if body.model_set:
        agent.model = body.model
        non_avatar_changed = True
    if body.runtime_set and body.runtime is not None:
        # Issue #73 — runtime change needs a respawn to take effect,
        # which ``bump_generation`` below will trigger.
        agent.runtime = body.runtime
        non_avatar_changed = True
    if body.avatar_kind_set:
        agent.avatar_kind = body.avatar_kind
        avatar_changed = True
    if body.avatar_value_set:
        agent.avatar_value = body.avatar_value
        avatar_changed = True
    if body.context_window_opt_out_set:
        # #148 Part 2 — pure server-side policy flag. The agent
        # subprocess reads its setting at spawn time (Part 3), so a
        # post-spawn toggle takes effect on the next bump_generation
        # restart. Counted as non_avatar_changed so the existing
        # generation-bump path handles the respawn.
        agent.context_window_opt_out = bool(body.context_window_opt_out)
        non_avatar_changed = True

    if non_avatar_changed or avatar_changed:
        await db.commit()
        await db.refresh(agent)

    # Bump generation and push sync to machine only when a non-avatar
    # field changed. Avatar-only edits reach the UI via the REST
    # response alone.
    if non_avatar_changed:
        lifecycle = request.app.state.agent_lifecycle
        await lifecycle.bump_generation(agent_id)

    return agent


# ── agent_files CRUD ────────────────────────────────────────────────
#
# These endpoints back the admin UI's per-agent file editor: list
# what's on disk, upsert individual files (skills, engine config),
# delete files. The materializer reconciles whatever we commit here
# on the next spawn — no separate "sync to disk" call needed.


@router.get("/{agent_id}/files", response_model=list[AgentFileOut])
async def list_agent_files(
    agent_id: str,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return every ``AgentFile`` row for an agent, sorted by path.

    Empty list is a valid response for a freshly-created agent
    that has not yet had any files attached.
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    rows = (
        await db.execute(
            select(AgentFile)
            .where(AgentFile.agent_id == agent_id)
            .order_by(AgentFile.path)
        )
    ).scalars().all()
    return list(rows)


@router.put("/{agent_id}/files", response_model=AgentFileOut)
async def upsert_agent_file(
    agent_id: str,
    body: AgentFileUpsert,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a single file under an agent.

    ``PUT`` semantics: whatever content the caller sends replaces
    whatever was there before at the same path. The unique
    ``(agent_id, path)`` constraint guarantees upsert-by-path.
    """
    try:
        validate_agent_file_path(body.path)
    except AgentFilePathError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"invalid file path {body.path!r}: {exc}",
        )

    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    existing = (
        await db.execute(
            select(AgentFile).where(
                AgentFile.agent_id == agent_id,
                AgentFile.path == body.path,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        row = AgentFile(agent_id=agent_id, path=body.path, content=body.content)
        db.add(row)
    else:
        existing.content = body.content
        row = existing

    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{agent_id}/files", status_code=200)
async def delete_agent_file(
    agent_id: str,
    body: AgentFileDelete,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single ``AgentFile`` row by path.

    Returns 404 if the file does not exist; 400 if the path is
    invalid (which would have been impossible to store in the
    first place — the check is a safety net).
    """
    try:
        validate_agent_file_path(body.path)
    except AgentFilePathError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"invalid file path {body.path!r}: {exc}",
        )

    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    existing = (
        await db.execute(
            select(AgentFile).where(
                AgentFile.agent_id == agent_id,
                AgentFile.path == body.path,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="File not found")

    await db.delete(existing)
    await db.commit()
    return {"deleted": True, "path": body.path}


@router.get("", response_model=list[AgentOut])
async def list_agents(
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """List all agents."""
    result = await db.execute(select(Agent).order_by(Agent.created_at))
    return list(result.scalars().all())


class EngineInfo(BaseModel):
    engine: str
    machine_count: int


@router.get("/engines/available", response_model=list[EngineInfo])
async def list_available_engines(
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return engines that have at least one online machine supporting them."""
    from sqlalchemy import func

    stmt = (
        select(
            MachineEngine.engine,
            func.count(Machine.id).label("machine_count"),
        )
        .join(Machine, Machine.id == MachineEngine.machine_id)
        .where(Machine.status == "online")
        .group_by(MachineEngine.engine)
        .order_by(MachineEngine.engine)
    )
    rows = (await db.execute(stmt)).all()
    return [EngineInfo(engine=row.engine, machine_count=row.machine_count) for row in rows]


class EngineModelOut(BaseModel):
    id: str
    label: str
    reasoning_levels: list[str]


class EngineCatalogOut(BaseModel):
    engine: str
    default_model: str
    models: list[EngineModelOut]
    reasoning_levels: list[str]


@router.get("/engines/{engine}/models", response_model=EngineCatalogOut)
async def get_engine_models(
    engine: str,
    identity: Identity = Depends(get_admin_identity),
):
    """Return the model catalog for ``engine``.

    The ``reasoning_levels`` on each model narrow the engine-level
    levels. When a model's ``reasoning_levels`` is empty, the
    engine-level list applies. Clients should union them as needed.
    """
    entry = get_engine_entry(engine)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown engine: {engine}")
    return EngineCatalogOut(
        engine=entry.engine,
        default_model=entry.default_model,
        models=[
            EngineModelOut(
                id=m.id,
                label=m.label,
                reasoning_levels=list(m.reasoning_levels),
            )
            for m in entry.models
        ],
        reasoning_levels=list(entry.reasoning_levels),
    )


@router.post("/{agent_id}/stop", status_code=200)
async def stop_agent(
    agent_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Stop a running agent (keeps the agent record and room assignments)."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.actual_state not in ("running", "starting", "pending"):
        return {"id": agent.id, "actual_state": agent.actual_state}

    lifecycle = request.app.state.agent_lifecycle
    await lifecycle.request_stop(agent.id)
    agent.desired_state = "stopped"
    await db.commit()
    await db.refresh(agent)
    return {"id": agent.id, "actual_state": agent.actual_state}


@router.delete("/{agent_id}", status_code=200)
async def delete_agent(
    agent_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Stop and fully remove an agent from the database."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Stop if running
    if agent.actual_state in ("running", "starting", "pending"):
        lifecycle = request.app.state.agent_lifecycle
        await lifecycle.request_stop(agent.id)

    # Locate the agent's DM rooms BEFORE wiping its Participant rows —
    # once the agent/user Participant link is gone we can't tell which
    # DM belonged to this agent. A DM here is any ``is_dm=True`` room
    # the agent currently participates in; there is normally exactly
    # one (auto-created at agent create time). Cascading FKs on Room
    # handle Participants / Messages / Tasks, so ``db.delete(room)``
    # is enough.
    dm_rooms = (
        await db.execute(
            select(Room)
            .join(Participant, Participant.room_id == Room.id)
            .where(Participant.agent_id == agent_id, Room.is_dm.is_(True))
        )
    ).scalars().all()

    # Clean up related records and delete agent
    await db.execute(delete(Participant).where(Participant.agent_id == agent_id))
    await db.execute(delete(AgentToken).where(AgentToken.agent_id == agent_id))
    for room in dm_rooms:
        await db.delete(room)
    await db.delete(agent)
    await db.commit()
    return {"deleted": True}


# ── Agent start/restart ─────────────────────────────────────────────


@router.post("/{agent_id}/start", status_code=200)
async def start_agent(
    agent_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Start or restart an agent."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check agent has rooms assigned
    room_result = await db.execute(
        select(Participant).where(Participant.agent_id == agent_id)
    )
    if not room_result.scalars().first():
        raise HTTPException(status_code=400, detail="Agent has no rooms assigned")

    lifecycle = request.app.state.agent_lifecycle

    # If running/starting/stopping, stop first
    if agent.actual_state in ("running", "starting", "stopping"):
        await lifecycle.request_stop(agent.id)

    # Reset to pending
    agent.actual_state = "pending"
    agent.desired_state = "running"
    agent.pid = None
    agent.placed_on_machine_id = None
    await db.commit()

    await lifecycle.request_start(agent.id)

    await db.refresh(agent)
    return AgentOut.model_validate(agent)


# ── Agent room management ───────────────────────────────────────────


class AgentRoomAdd(BaseModel):
    room_id: str


class AgentRoomOut(BaseModel):
    room_id: str
    room_name: str = ""
    role: str
    # Surfaced so the admin UI can hide DM rooms from the "Manage
    # rooms" dialog — the DM is a fixed 1:1 channel and showing it
    # alongside regular rooms invites admins to accidentally detach
    # it. Keeping it in the payload (rather than filtering
    # server-side) lets other callers keep seeing DM rooms if they
    # need to.
    is_dm: bool = False


@router.get("/{agent_id}/rooms", response_model=list[AgentRoomOut])
async def list_agent_rooms(
    agent_id: str,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """List rooms an agent is a participant of, with room names."""
    from doorae.db.models import Room

    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    result = await db.execute(
        select(Participant, Room.name, Room.is_dm)
        .join(Room, Room.id == Participant.room_id)
        .where(Participant.agent_id == agent_id)
    )
    return [
        AgentRoomOut(
            room_id=p.room_id,
            room_name=name or "",
            role=p.role,
            is_dm=is_dm,
        )
        for p, name, is_dm in result.all()
    ]


@router.post("/{agent_id}/rooms", status_code=201, response_model=AgentRoomOut)
async def add_agent_room(
    agent_id: str,
    body: AgentRoomAdd,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Add an agent to a room. Triggers start if agent is idle."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Explicit duplicate check stays *before* ``ensure_agent_in_room``.
    # The helper is idempotent (returns the existing row when called
    # twice) which is the right semantics for ``POST /participants``
    # but the admin API contract here is "409 on repeat add" — the
    # frontend's confirmation UX depends on that distinct error code.
    existing = await db.execute(
        select(Participant).where(
            Participant.agent_id == agent_id,
            Participant.room_id == body.room_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Agent already in room")

    # #227 — route through ``ensure_agent_in_room`` so this endpoint
    # shares the JoinRoomOut fan-out and drop-observability with
    # ``POST /rooms/{id}/participants``. Before #227 the two paths
    # diverged: the agents-API route inserted the row directly and
    # skipped the WS notification entirely, meaning the agent's
    # SDK *never* heard about the new room until process restart
    # even for dormant agents that got redispatched via request_start
    # immediately after.
    manager = getattr(request.app.state, "connection_manager", None)
    await ensure_agent_in_room(
        db,
        manager,
        room_id=body.room_id,
        agent_id=agent_id,
        role="member",
    )

    # #227 — single dispatch policy shared with ``rooms/router.py``:
    # ``on_room_added`` decides between ``request_start`` (dormant
    # agents, including the 2026-04-12 "서브에이전트1/2" pending case)
    # and ``bump_generation`` (running/starting agents — the bug this
    # issue fixes: the machine re-spawns with refreshed ``--room``
    # args instead of relying on the silently-droppable WS push).
    lifecycle = getattr(request.app.state, "agent_lifecycle", None)
    if lifecycle is not None:
        if agent.actual_state in ("idle", "stopped", "crashed", "pending"):
            # Preserve the pre-#227 eager state flip so the admin UI
            # reflects the new desired/pending pair before the
            # machine's first heartbeat. ``request_start`` would do
            # this itself but only after the placement query — the
            # flip here keeps the UX snappy.
            agent.actual_state = "pending"
            agent.desired_state = "running"
            await db.commit()
        await lifecycle.on_room_added(agent.id)
        await db.refresh(agent)

    room = (
        await db.execute(select(Room).where(Room.id == body.room_id))
    ).scalar_one_or_none()
    return AgentRoomOut(room_id=body.room_id, room_name=room.name if room else "", role="member")


@router.delete("/{agent_id}/rooms/{room_id}", status_code=200)
async def remove_agent_room(
    agent_id: str,
    room_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Remove an agent from a room. Stops the agent if no rooms left."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Find and delete the participation
    result = await db.execute(
        select(Participant).where(
            Participant.agent_id == agent_id,
            Participant.room_id == room_id,
        )
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status_code=404, detail="Agent not in room")

    await db.delete(participant)
    await db.commit()

    # Check if agent has any remaining rooms
    remaining = await db.execute(
        select(Participant).where(Participant.agent_id == agent_id)
    )
    if not remaining.scalars().first():
        # No rooms left — stop the agent
        if agent.actual_state in ("running", "starting", "pending"):
            lifecycle = request.app.state.agent_lifecycle
            await lifecycle.request_stop(agent.id)

    return {"removed": True}


# ── Activity log ───────────────────────────────────────────────────


class ActivityLogOut(BaseModel):
    id: str
    agent_id: str
    event_type: str
    timestamp: str
    # #222 — exposed so the client can group rows by turn without
    # parsing the ``details`` JSON. Null for system events
    # (start_requested / stop_requested / state_changed / ...) that
    # don't belong to any particular request lifecycle.
    request_id: str | None = None
    details: dict | None = None


@router.get("/{agent_id}/activity", response_model=list[ActivityLogOut])
async def get_agent_activity(
    agent_id: str,
    limit: int = 50,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return recent activity events for an agent."""
    stmt = (
        select(ActivityLog)
        .where(ActivityLog.agent_id == agent_id)
        .order_by(ActivityLog.timestamp.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        ActivityLogOut(
            id=r.id,
            agent_id=r.agent_id,
            event_type=r.event_type,
            timestamp=r.timestamp.isoformat(),
            request_id=r.request_id,
            details=r.details,
        )
        for r in rows
    ]
