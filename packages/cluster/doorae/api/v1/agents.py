"""REST endpoints for Agent lifecycle — ``/api/v1/agents``."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.agent_files import AgentFilePathError, validate_agent_file_path
from doorae.auth.dependencies import Identity
from doorae.db.models import (
    ActivityLog,
    Agent,
    AgentFile,
    AgentToken,
    LLMGatewayModel,
    Machine,
    MachineEngine,
    Participant,
    Room,
    Task,
)
from doorae.dependencies import get_admin_identity, get_db
from doorae.engines import get_engine_entry
from doorae.rooms.membership import ensure_agent_in_room
from doorae.scheduler.gateway_secrets import openhands_model_id_for_gateway

if TYPE_CHECKING:
    from doorae.scheduler.machine_bus import MachineBus

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
    # Issue #271 — short public-facing introduction visible to other
    # participants (LLM roster + mention popover + participant list).
    # Capped at 200 chars to keep the per-turn token cost predictable
    # when the agent runtime appends it inline to every system prompt.
    description: Optional[str] = Field(default=None, max_length=200)
    # Issue #279 — collaboration policy. ``solo`` (default) preserves
    # pre-#279 behaviour. ``collaborative`` makes the agent SDK append a
    # peer-mention usage hint to the LLM system prompt so the agent
    # delegates via mentions and synthesizes peer replies. Validated by
    # the field pattern.
    collaboration_mode: str = Field(
        default="solo", pattern="^(solo|collaborative)$"
    )


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
    # Issue #309 — semantic permission tier. Validated against the
    # tier set; ``_set`` flag follows the established pattern so a
    # rename PATCH can't silently flip the level. Admin-only is
    # enforced in the PATCH handler via the ``Identity.is_admin``
    # check, not the schema.
    permission_level: Optional[str] = Field(
        default=None, pattern="^(restricted|standard|trusted)$"
    )
    permission_level_set: bool = False
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
    # Issue #237 — admin editable memory_md. Same ``_set`` flag pattern
    # as ``agents_md``: explicit opt-in lets an admin clear the field
    # (send ``memory_md=None, memory_md_set=True``) while omitting the
    # field leaves the stored value untouched. Machine-level syncs
    # write here too (machine -> DB flush on file change).
    memory_md: Optional[str] = None
    memory_md_set: bool = False
    # Issue #271 — public-facing introduction. ``_set`` flag follows
    # the established pattern so an admin can explicitly clear the
    # field (send ``description=None, description_set=True``) while
    # leaving an unrelated PATCH from touching it. 200-char cap mirrors
    # ``AgentCreate``.
    description: Optional[str] = Field(default=None, max_length=200)
    description_set: bool = False
    # Issue #279 — collaboration policy toggle. Same ``_set`` flag
    # pattern so a PATCH that only renames the agent doesn't silently
    # reset the mode back to ``solo``.
    collaboration_mode: Optional[str] = Field(
        default=None, pattern="^(solo|collaborative)$"
    )
    collaboration_mode_set: bool = False


class AgentOut(BaseModel):
    id: str
    name: str
    engine: str
    desired_state: str
    actual_state: str
    placed_on_machine_id: Optional[str] = None
    machine_online: bool = False
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
    # Issue #309 — semantic permission tier. NULL means the adapter
    # falls back to the ``standard`` tier (= pre-#309 hardcoded
    # behaviour); the UI renders NULL as "Default".
    permission_level: Optional[str] = None
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
    # Issue #237 — per-agent long-term memory snapshot (markdown). None
    # for agents that have never written anything; the file at
    # ``~/.doorae/agents/<id>/memory/notes.md`` on the hosting machine
    # is the runtime truth. Exposed so the admin UI can render / edit
    # the scratchpad.
    memory_md: Optional[str] = None
    # Issue #271 — public-facing self-introduction. None for agents
    # that have never set one; otherwise capped at 200 chars and
    # surfaced through the WS welcome frame to peers and the LLM roster.
    description: Optional[str] = None
    # Issue #279 — collaboration policy surfaced so the admin UI can
    # render a toggle without a second query.
    collaboration_mode: str = "solo"
    model_config = {"from_attributes": True, "protected_namespaces": ()}


def _agent_to_out(agent: Agent, machine_bus: MachineBus | None) -> AgentOut:
    out = AgentOut.model_validate(agent)
    out.machine_online = bool(
        agent.placed_on_machine_id
        and machine_bus
        and machine_bus.is_connected(agent.placed_on_machine_id)
    )
    return out


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
        description=body.description,
        collaboration_mode=body.collaboration_mode,
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
    # #237 — stamp ``representative_agent_id`` so the per-agent DM
    # list endpoint (``GET /api/v1/rooms?is_dm=true&representative_agent_id=<id>``)
    # can fan out to every DM an agent owns, not just the auto-created
    # first one.
    dm_room = Room(
        project_id=None,
        name=f"DM: {agent.name}",
        is_dm=True,
        representative_agent_id=agent.id,
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

    return _agent_to_out(agent, request.app.state.machine_bus)


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

    # Two change counters so peer-only metadata edits can skip the
    # ``bump_generation`` call: avatars and descriptions are read by
    # *other* clients/agents (UI rendering, peer LLM rosters), never
    # by this agent's own subprocess, so restarting it for a metadata
    # swap would be surprising. Any field the subprocess actually
    # consumes flips ``runtime_changed`` and keeps the existing
    # "mutate → generation bump → respawn" semantics.
    runtime_changed = False
    peer_metadata_changed = False
    if body.name is not None:
        agent.name = body.name
        runtime_changed = True
    if body.agents_md_set:
        # Explicit opt-in flag is needed to distinguish "omit the
        # field" (no change) from "set the field to null" (clear
        # the role/rules body).
        agent.agents_md = body.agents_md
        runtime_changed = True
    if body.reasoning_effort_set:
        agent.reasoning_effort = body.reasoning_effort
        runtime_changed = True
    if body.model_set:
        agent.model = body.model
        runtime_changed = True
    if body.permission_level_set:
        # #309 — admin-only permission tier. The pattern matches
        # ``reasoning_effort`` / ``model``: capture the previous
        # value, swap, write an ``ActivityLog`` row tagged
        # ``agent_permission_changed`` so security-relevant
        # transitions are auditable. Validation (one of
        # restricted/standard/trusted, or null) is enforced by the
        # Pydantic field pattern; here we only record the change.
        previous_permission = agent.permission_level
        agent.permission_level = body.permission_level
        if previous_permission != body.permission_level:
            db.add(
                ActivityLog(
                    agent_id=agent.id,
                    event_type="agent_permission_changed",
                    details={
                        "from": previous_permission,
                        "to": body.permission_level,
                        "by_user_id": identity.id
                        if identity.kind == "user"
                        else None,
                    },
                )
            )
        runtime_changed = True
    if body.runtime_set and body.runtime is not None:
        # Issue #73 — runtime change needs a respawn to take effect,
        # which ``bump_generation`` below will trigger.
        agent.runtime = body.runtime
        runtime_changed = True
    if body.avatar_kind_set:
        agent.avatar_kind = body.avatar_kind
        peer_metadata_changed = True
    if body.avatar_value_set:
        agent.avatar_value = body.avatar_value
        peer_metadata_changed = True
    if body.context_window_opt_out_set:
        # #148 Part 2 — pure server-side policy flag. The agent
        # subprocess reads its setting at spawn time (Part 3), so a
        # post-spawn toggle takes effect on the next bump_generation
        # restart.
        agent.context_window_opt_out = bool(body.context_window_opt_out)
        runtime_changed = True
    if body.memory_md_set:
        # #237 — admin manually edited the memory scratchpad. This
        # becomes the new DB-side snapshot; the next spawn / resume
        # will materialize it to ``memory/notes.md`` on the hosting
        # machine. The restart picks up the new content.
        agent.memory_md = body.memory_md
        runtime_changed = True
    if body.description_set:
        # #271 — public-facing introduction. The agent itself never
        # consumes this field at runtime; only *peers* see it via the
        # WS welcome frame's ``ParticipantBrief.description``. Restarting
        # this agent's subprocess would do nothing for that propagation,
        # so it's treated as peer metadata. Peers pick up the new value
        # on their next welcome (room join, reconnect, or new spawn).
        agent.description = body.description
        peer_metadata_changed = True
    if body.collaboration_mode_set and body.collaboration_mode is not None:
        # #279 — the agent SDK reads this on every welcome frame and
        # uses it to decide whether to append the peer-mention hint to
        # the LLM system prompt. Treated as peer metadata: peers see
        # the new value on their next welcome (a reconnect or new
        # message turn rebuilds the system prompt), no respawn needed.
        agent.collaboration_mode = body.collaboration_mode
        peer_metadata_changed = True

    if runtime_changed or peer_metadata_changed:
        await db.commit()
        await db.refresh(agent)

    # Bump generation and push sync to machine only when a field the
    # subprocess actually reads has changed. Peer-metadata-only edits
    # reach the UI via the REST response alone.
    if runtime_changed:
        lifecycle = request.app.state.agent_lifecycle
        await lifecycle.bump_generation(agent_id)

    return _agent_to_out(agent, request.app.state.machine_bus)


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
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """List all agents."""
    result = await db.execute(select(Agent).order_by(Agent.created_at))
    machine_bus = getattr(request.app.state, "machine_bus", None)
    return [_agent_to_out(agent, machine_bus) for agent in result.scalars().all()]


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return a single agent by id.

    #237 surfaced the need for a single-agent read path so the admin UI
    can fetch ``memory_md`` in isolation. The same endpoint also keeps
    callers from having to scan ``list_agents`` for one row.
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _agent_to_out(agent, getattr(request.app.state, "machine_bus", None))


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
    # Marker for UI to distinguish static catalog entries from gateway-
    # registered models. ``"builtin"`` is the existing hand-curated list
    # in ``engines/catalog.py``; ``"gateway"`` is populated at request
    # time from ``llm_gateway_models``.
    source: str = "builtin"


class EngineCatalogOut(BaseModel):
    engine: str
    default_model: str
    models: list[EngineModelOut]
    reasoning_levels: list[str]


@router.get("/engines/{engine}/models", response_model=EngineCatalogOut)
async def get_engine_models(
    engine: str,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return the model catalog for ``engine``.

    The ``reasoning_levels`` on each model narrow the engine-level
    levels. When a model's ``reasoning_levels`` is empty, the
    engine-level list applies. Clients should union them as needed.

    Issue #359 — for the openhands engine, also surfaces models the
    operator has registered in ``llm_gateway_models``. They land in
    the response with ``source="gateway"`` so the UI can badge them
    distinctly from the static catalog. Other engines keep their
    pre-#359 behaviour (catalog only) because the gateway path
    currently only flows engine_secrets to openhands; surfacing
    gateway models elsewhere would advertise a route the agent can't
    actually use.
    """
    entry = get_engine_entry(engine)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown engine: {engine}")

    models: list[EngineModelOut] = [
        EngineModelOut(
            id=m.id,
            label=m.label,
            reasoning_levels=list(m.reasoning_levels),
            source="builtin",
        )
        for m in entry.models
    ]

    if engine == "openhands":
        # Append rows from the gateway table. ``enabled=False`` rows
        # exist (admin can pause a model without deleting) so we
        # filter explicitly. ``reasoning_levels`` is left empty —
        # gateway-registered models have no doorae-curated effort
        # taxonomy; clients fall back to the engine-level list per
        # the existing contract documented in the docstring above.
        gw_rows = (
            await db.execute(
                select(LLMGatewayModel)
                .where(LLMGatewayModel.enabled.is_(True))
                .order_by(LLMGatewayModel.model_name)
            )
        ).scalars().all()
        existing_ids = {m.id for m in models}
        for row in gw_rows:
            model_id = openhands_model_id_for_gateway(
                row.provider, row.model_name
            )
            if model_id is None:
                continue
            if model_id in existing_ids:
                # A static catalog entry with the same id wins —
                # operator-registered duplicates would only confuse
                # the picker. Skip silently.
                continue
            models.append(
                EngineModelOut(
                    id=model_id,
                    label=f"{row.model_name} (via gateway)",
                    reasoning_levels=[],
                    source="gateway",
                )
            )

    return EngineCatalogOut(
        engine=entry.engine,
        default_model=entry.default_model,
        models=models,
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
    lifecycle = request.app.state.agent_lifecycle
    if agent.actual_state in ("running", "starting", "pending"):
        await lifecycle.request_stop(agent.id)
    # Issue #369 — evict the per-agent token cache even when the
    # agent wasn't running. ``request_stop`` only fires on the
    # active-state branch, so a delete on an already-stopped agent
    # would leave a stale cache entry behind otherwise.
    lifecycle.evict_token(agent.id)

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
    return _agent_to_out(agent, request.app.state.machine_bus)


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


# ── Per-agent DMs (#237) ────────────────────────────────────────────
#
# The admin UI's sidebar renders a tree ``Agent → DM[]`` so users can
# split a long-running conversation into multiple rooms (cold-start
# SDK sessions per room). Each DM is a normal ``Room`` with
# ``is_dm=True`` and ``representative_agent_id`` pointing at the
# agent, plus two Participant rows (caller + agent). Creation is
# triggered by the sidebar's "+ 새 대화" button in the AgentNode.


class AgentDMCreate(BaseModel):
    """Body for ``POST /agents/{id}/dms``.

    ``name`` is optional — if the caller omits it the server mints
    ``"DM: <agent.name> #<N>"`` where N is the count of the caller's
    existing DMs with this agent + 1. Always returning a unique name
    keeps the sidebar reading cleanly when it renders the tree.
    """

    name: Optional[str] = None


@router.post("/{agent_id}/dms", status_code=status.HTTP_201_CREATED)
async def create_agent_dm(
    agent_id: str,
    body: AgentDMCreate,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Create a new DM room bound to ``agent_id``.

    Mirrors the auto-DM path in ``create_agent`` but never consults /
    reuses an existing DM: each call yields a fresh room with a fresh
    SDK session. The caller is added as ``owner`` so they can later
    toggle ephemeral / rename / delete without needing the global
    admin claim.

    Returns the room payload in the same shape the sidebar expects
    (matches ``RoomOut`` shape). ``representative_agent_id`` is always
    populated so the list filter at
    ``GET /api/v1/rooms?is_dm=true&representative_agent_id=<id>`` picks
    it up immediately.
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Auto-name from existing DM count when caller omits ``name``.
    if body.name:
        dm_name = body.name
    else:
        existing = (
            await db.execute(
                select(Room)
                .where(Room.is_dm.is_(True))
                .where(Room.representative_agent_id == agent_id)
                .join(Participant, Participant.room_id == Room.id)
                .where(Participant.user_id == identity.id)
            )
        ).scalars().all()
        dm_name = f"DM: {agent.name} #{len(existing) + 1}"

    room = Room(
        project_id=None,
        name=dm_name,
        is_dm=True,
        representative_agent_id=agent_id,
    )
    db.add(room)
    await db.flush()
    db.add(Participant(room_id=room.id, user_id=identity.id, role="owner"))
    await db.flush()

    # Register the agent as a participant via the shared helper so the
    # JoinRoomOut frame fires (same invariant ``create_agent`` relies
    # on — the helper adds the row idempotently).
    manager = getattr(request.app.state, "connection_manager", None)
    await ensure_agent_in_room(
        db,
        manager,
        room_id=room.id,
        agent_id=agent_id,
        role="member",
    )

    # Re-dispatch lifecycle so the machine picks up the new room.
    lifecycle = getattr(request.app.state, "agent_lifecycle", None)
    if lifecycle is not None:
        await lifecycle.on_room_added(agent_id)

    await db.commit()
    await db.refresh(room)
    return {
        "id": room.id,
        "project_id": room.project_id,
        "name": room.name,
        "description": room.description,
        "parent_room_id": room.parent_room_id,
        "is_dm": room.is_dm,
        "representative_agent_id": room.representative_agent_id,
        "context_window_enabled": room.context_window_enabled,
        "speaker_strategy": room.speaker_strategy,
        "orchestrator_agent_id": room.orchestrator_agent_id,
        "ephemeral": room.ephemeral,
    }


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


# ── Per-agent task aggregation (#266) ─────────────────────────────


class AgentTaskOut(BaseModel):
    """Task row enriched with its originating room — backs the 2차 뷰
    in the agent profile (plan §3.1, Step 9). Mirrors ``TaskOut`` fields
    plus ``room_name`` so the frontend can render room-name chips
    without a second round-trip."""

    id: str
    room_id: str
    room_name: str
    title: str
    status: str
    assignee_participant_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str


@router.get("/{agent_id}/tasks", response_model=list[AgentTaskOut])
async def list_agent_tasks(
    agent_id: str,
    status: Optional[str] = None,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return every task assigned to *agent_id* across all rooms.

    Joins ``tasks`` to ``participants`` on ``assignee_participant_id``
    and filters by ``participants.agent_id``. Unassigned tasks (the
    NULL assignee path) are excluded by construction — the join key
    is the assignee. Phase 1 권한: admin-only (plan §3.2 결정 3).
    """
    stmt = (
        select(Task, Room.name)
        .join(Participant, Task.assignee_participant_id == Participant.id)
        .join(Room, Task.room_id == Room.id)
        .where(Participant.agent_id == agent_id)
        .order_by(Task.created_at)
    )
    if status:
        stmt = stmt.where(Task.status == status)
    rows = (await db.execute(stmt)).all()
    return [
        AgentTaskOut(
            id=task.id,
            room_id=task.room_id,
            room_name=room_name,
            title=task.title,
            status=task.status,
            assignee_participant_id=task.assignee_participant_id,
            created_by=task.created_by,
            created_at=task.created_at.isoformat(),
        )
        for task, room_name in rows
    ]


# Terminal statuses are the only ones an admin can sweep for an agent.
# Active states (todo/in_progress/blocked) are owned by the agent runtime
# and clearing them out from under the loop is a recipe for stuck work.
# (#320 plan §3.1)
_TERMINAL_TASK_STATUSES = frozenset({"done", "failed"})


@router.delete("/{agent_id}/tasks")
async def bulk_delete_agent_tasks(
    agent_id: str,
    status: str,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
):
    """Delete every terminal-state task assigned to *agent_id*.

    Powers the "Clear all" button on the agent settings Tasks panel
    (#320). Same join contract as ``list_agent_tasks`` — scoped by
    ``assignee_participant_id`` → ``Participant.agent_id`` — so the
    sweep never touches another agent's rows in the same room. Only
    terminal statuses (``done``, ``failed``) are accepted; active states
    are rejected at the boundary with 400.
    """
    if status not in _TERMINAL_TASK_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                "status must be one of: " + ", ".join(sorted(_TERMINAL_TASK_STATUSES))
            ),
        )

    # Two-step delete keeps us inside SQLAlchemy's "no synchronize-able
    # delete with a join" guarantee on SQLite — resolve the IDs first,
    # then delete by primary key.
    id_stmt = (
        select(Task.id)
        .join(Participant, Task.assignee_participant_id == Participant.id)
        .where(Participant.agent_id == agent_id)
        .where(Task.status == status)
    )
    target_ids = list((await db.execute(id_stmt)).scalars().all())
    if target_ids:
        await db.execute(delete(Task).where(Task.id.in_(target_ids)))
        await db.commit()
    return {"deleted_count": len(target_ids)}


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
