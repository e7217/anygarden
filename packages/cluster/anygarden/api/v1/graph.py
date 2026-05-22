"""Topology graph endpoint — ``GET /api/v1/graph`` (#58).

Returns a snapshot of the User × Machine × Agent × Room × Project
relationship graph. Admins see the whole cluster (``scope=global``);
regular users see only the slice they own or participate in
(``scope=personal``).

The payload is built in a single transaction with a bounded number
of column-scoped SELECTs (enforced by an N+1 budget test) to avoid
per-row fetches, ETag-hashed so unchanged graphs short-circuit to
``304`` on re-fetch, and capped by a ``private, max-age=5`` cache
directive so browsers don't thrash the endpoint during rapid
navigation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, Machine, Participant, Project, Room, User
from anygarden.dependencies import forbid_guest, get_db

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


# ── Pydantic response schemas ───────────────────────────────────────


NodeKind = Literal["user", "machine", "agent", "room", "project"]
EdgeKind = Literal["owns", "places", "participates", "parent_of"]
ScopeKind = Literal["personal", "global", "auto"]


class NodeOut(BaseModel):
    id: str
    kind: NodeKind
    label: str
    data: dict = Field(default_factory=dict)


class EdgeOut(BaseModel):
    id: str
    source: str
    target: str
    kind: EdgeKind
    data: dict = Field(default_factory=dict)


class GraphOut(BaseModel):
    generated_at: str
    scope: Literal["personal", "global"]
    nodes: list[NodeOut]
    edges: list[EdgeOut]


# ── ID prefix helpers ────────────────────────────────────────────────


def _uid(user_id: str) -> str:
    return f"u_{user_id}"


def _mid(machine_id: str) -> str:
    return f"m_{machine_id}"


def _aid(agent_id: str) -> str:
    return f"a_{agent_id}"


def _rid(room_id: str) -> str:
    return f"r_{room_id}"


def _pid(project_id: str) -> str:
    return f"p_{project_id}"


# ── Scope resolution ────────────────────────────────────────────────


def _resolve_scope(identity: Identity, requested: ScopeKind) -> Literal["personal", "global"]:
    """Map the requested scope to either ``personal`` or ``global``.

    Admin:
      - ``global``  -> global
      - ``personal`` -> personal (admin opts in to their own slice)
      - ``auto``    -> global
    Regular user:
      - ``global``  -> 403 (security)
      - ``personal`` -> personal
      - ``auto``    -> personal
    """
    is_admin = bool(
        identity.kind == "user"
        and identity.claims is not None
        and getattr(identity.claims, "is_admin", False)
    )
    if requested == "global":
        if not is_admin:
            raise HTTPException(status_code=403, detail="Admin scope required")
        return "global"
    if requested == "personal":
        return "personal"
    # auto
    return "global" if is_admin else "personal"


# ── Graph builders ──────────────────────────────────────────────────


def _is_typing_for(typing_tracker: object | None, room_id: str) -> bool:
    """Return True iff at least one participant is currently typing in
    *room_id*.

    Defensive against missing ``app.state.typing_tracker`` (e.g. in
    test apps that haven't run the lifespan startup yet) — callers
    pass whatever ``getattr(app.state, "typing_tracker", None)``
    yielded, and we degrade silently to ``False``. ``TypingTracker``'s
    own ``get_typing`` already filters stale TTL entries, so we don't
    need to re-validate them here (see
    ``anygarden.orchestration.rules.TypingTracker.get_typing``).
    """
    if typing_tracker is None:
        return False
    get = getattr(typing_tracker, "get_typing", None)
    if get is None:
        return False
    try:
        active = get(room_id)
    except Exception:  # pragma: no cover — defensive
        return False
    return bool(active)


async def _build_global_graph(
    db: AsyncSession, typing_tracker: object | None = None
) -> tuple[list[NodeOut], list[EdgeOut]]:
    """Fetch the full graph. All rows, bulk SELECTs, no per-entity round-trips."""
    users = (await db.execute(select(User))).scalars().all()
    machines = (await db.execute(select(Machine))).scalars().all()
    agents = (await db.execute(select(Agent))).scalars().all()
    rooms = (await db.execute(select(Room))).scalars().all()
    projects = (await db.execute(select(Project))).scalars().all()
    participants = (await db.execute(select(Participant))).scalars().all()

    # Build agent count per machine for the machine node's ``data``.
    agent_count_by_machine: dict[str, int] = {}
    for a in agents:
        if a.placed_on_machine_id:
            agent_count_by_machine[a.placed_on_machine_id] = (
                agent_count_by_machine.get(a.placed_on_machine_id, 0) + 1
            )

    # Participant-count per room (counting both user + agent participants).
    participant_count_by_room: dict[str, int] = {}
    for p in participants:
        participant_count_by_room[p.room_id] = (
            participant_count_by_room.get(p.room_id, 0) + 1
        )

    nodes: list[NodeOut] = []
    known_room_ids: set[str] = {r.id for r in rooms}

    for u in users:
        if u.is_anonymous:
            # Guest rows are transient — exclude from the topology view.
            continue
        nodes.append(
            NodeOut(
                id=_uid(u.id),
                kind="user",
                label=u.email or (u.display_name or "user"),
                data={
                    "is_admin": bool(u.is_admin),
                    "is_anonymous": bool(u.is_anonymous),
                    "display_name": u.display_name,
                },
            )
        )

    for m in machines:
        nodes.append(
            NodeOut(
                id=_mid(m.id),
                kind="machine",
                label=m.name,
                data={
                    "status": m.status,
                    "hostname": m.hostname,
                    "daemon_version": m.daemon_version,
                    "owner_user_id": m.owner_user_id,
                    "agent_count": agent_count_by_machine.get(m.id, 0),
                },
            )
        )

    for a in agents:
        nodes.append(
            NodeOut(
                id=_aid(a.id),
                kind="agent",
                label=a.name,
                data={
                    "engine": a.engine,
                    "actual_state": a.actual_state,
                    "desired_state": a.desired_state,
                    "model": a.model,
                    # #309 — surface the permission tier so the
                    # AgentNode can render a small ⚠ when the agent
                    # is ``trusted`` (host access). NULL/standard
                    # leaves the node visually unchanged.
                    "permission_level": a.permission_level,
                    "placed_on_machine_id": a.placed_on_machine_id,
                    "last_heartbeat_at": (
                        a.last_heartbeat_at.isoformat()
                        if a.last_heartbeat_at is not None
                        else None
                    ),
                    "last_crash_reason": a.last_crash_reason,
                },
            )
        )

    for r in rooms:
        nodes.append(
            NodeOut(
                id=_rid(r.id),
                kind="room",
                label=r.name,
                data={
                    "is_dm": bool(r.is_dm),
                    "project_id": r.project_id,
                    "parent_room_id": r.parent_room_id,
                    "participant_count": participant_count_by_room.get(r.id, 0),
                    "representative_agent_id": r.representative_agent_id,
                    "is_typing": _is_typing_for(typing_tracker, r.id),
                },
            )
        )

    for p in projects:
        nodes.append(
            NodeOut(
                id=_pid(p.id),
                kind="project",
                label=p.name,
                data={"description": p.description},
            )
        )

    edges = _assemble_edges(
        users=users,
        machines=machines,
        agents=agents,
        rooms=rooms,
        participants=participants,
        known_room_ids=known_room_ids,
    )

    return nodes, edges


async def _build_personal_graph(
    db: AsyncSession, user_id: str, typing_tracker: object | None = None
) -> tuple[list[NodeOut], list[EdgeOut]]:
    """Return the slice visible to the given user.

    Invariant: NO other users' ids may appear anywhere in the result.
    Personal slice consists of:

    * the calling user themselves
    * machines they own
    * rooms they participate in (+ their agent co-members)
    * agents placed on their machines (+ their room memberships
      restricted to rooms they already have access to)
    * projects that contain the rooms above
    """
    me = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if me is None:
        # The identity layer guarantees the caller's user row exists;
        # hitting this branch means the DB was tampered with under us.
        raise HTTPException(status_code=404, detail="User not found")

    owned_machines = list(
        (
            await db.execute(select(Machine).where(Machine.owner_user_id == user_id))
        ).scalars().all()
    )
    owned_machine_ids = {m.id for m in owned_machines}

    # Rooms I participate in (through a Participant row).
    my_participants_rows = list(
        (
            await db.execute(
                select(Participant).where(Participant.user_id == user_id)
            )
        ).scalars().all()
    )
    my_room_ids: set[str] = {p.room_id for p in my_participants_rows}

    # Union of my machines + agents in my rooms.
    agents_on_my_machines = []
    if owned_machine_ids:
        agents_on_my_machines = list(
            (
                await db.execute(
                    select(Agent).where(
                        Agent.placed_on_machine_id.in_(owned_machine_ids)
                    )
                )
            ).scalars().all()
        )

    # Participants of my rooms — used to discover *agents* that share my
    # rooms and to count members. We intentionally only expose the agent
    # participants, never the other users' ids.
    room_participants: list[Participant] = []
    if my_room_ids:
        room_participants = list(
            (
                await db.execute(
                    select(Participant).where(Participant.room_id.in_(my_room_ids))
                )
            ).scalars().all()
        )

    agents_in_my_rooms_ids: set[str] = {
        p.agent_id for p in room_participants if p.agent_id is not None
    }
    agents_in_my_rooms: list[Agent] = []
    # Agents that appear via room membership but aren't placed on my
    # machines still need to be fetched so we can render their node.
    additional_agent_ids = agents_in_my_rooms_ids - {a.id for a in agents_on_my_machines}
    if additional_agent_ids:
        agents_in_my_rooms = list(
            (
                await db.execute(
                    select(Agent).where(Agent.id.in_(additional_agent_ids))
                )
            ).scalars().all()
        )

    all_agents = agents_on_my_machines + agents_in_my_rooms
    all_agent_ids = {a.id for a in all_agents}

    # Rooms I can see. Extended by rooms represented by any of the
    # visible agents would leak — stay strict: only my rooms. Also drop
    # rooms whose parent I don't know (parent_room_id belongs to a room
    # I am not a member of) from the parent_of edge set later.
    rooms_list: list[Room] = []
    if my_room_ids:
        rooms_list = list(
            (
                await db.execute(select(Room).where(Room.id.in_(my_room_ids)))
            ).scalars().all()
        )

    # #179 — DM rooms carry ``project_id=NULL``; exclude them from the
    # project fan-out so ``Project.id.in_({..., None})`` doesn't degrade
    # into an always-false SQL predicate on some dialects.
    project_ids = {r.project_id for r in rooms_list if r.project_id is not None}
    projects_list: list[Project] = []
    if project_ids:
        projects_list = list(
            (
                await db.execute(select(Project).where(Project.id.in_(project_ids)))
            ).scalars().all()
        )

    # Agent count per owned machine.
    agent_count_by_machine: dict[str, int] = {}
    for a in agents_on_my_machines:
        if a.placed_on_machine_id:
            agent_count_by_machine[a.placed_on_machine_id] = (
                agent_count_by_machine.get(a.placed_on_machine_id, 0) + 1
            )

    # Participant count per visible room.
    participant_count_by_room: dict[str, int] = {}
    for p in room_participants:
        participant_count_by_room[p.room_id] = (
            participant_count_by_room.get(p.room_id, 0) + 1
        )

    nodes: list[NodeOut] = []
    nodes.append(
        NodeOut(
            id=_uid(me.id),
            kind="user",
            label=me.email or (me.display_name or "me"),
            data={
                "is_admin": bool(me.is_admin),
                "is_anonymous": bool(me.is_anonymous),
                "display_name": me.display_name,
            },
        )
    )
    for m in owned_machines:
        nodes.append(
            NodeOut(
                id=_mid(m.id),
                kind="machine",
                label=m.name,
                data={
                    "status": m.status,
                    "hostname": m.hostname,
                    "daemon_version": m.daemon_version,
                    "owner_user_id": m.owner_user_id,
                    "agent_count": agent_count_by_machine.get(m.id, 0),
                },
            )
        )
    for a in all_agents:
        # Null-out placed_on when the placement machine isn't mine —
        # otherwise we'd leak a foreign machine id through the
        # agent.data.placed_on_machine_id field.
        safe_placement = (
            a.placed_on_machine_id if a.placed_on_machine_id in owned_machine_ids else None
        )
        nodes.append(
            NodeOut(
                id=_aid(a.id),
                kind="agent",
                label=a.name,
                data={
                    "engine": a.engine,
                    "actual_state": a.actual_state,
                    "desired_state": a.desired_state,
                    "model": a.model,
                    # #309 — surface the permission tier so the
                    # AgentNode can render a small ⚠ when the agent
                    # is ``trusted`` (host access). NULL/standard
                    # leaves the node visually unchanged.
                    "permission_level": a.permission_level,
                    "placed_on_machine_id": safe_placement,
                    "last_heartbeat_at": (
                        a.last_heartbeat_at.isoformat()
                        if a.last_heartbeat_at is not None
                        else None
                    ),
                    "last_crash_reason": a.last_crash_reason,
                },
            )
        )
    known_room_ids = {r.id for r in rooms_list}
    for r in rooms_list:
        safe_parent = r.parent_room_id if r.parent_room_id in known_room_ids else None
        safe_rep = (
            r.representative_agent_id
            if r.representative_agent_id in all_agent_ids
            else None
        )
        nodes.append(
            NodeOut(
                id=_rid(r.id),
                kind="room",
                label=r.name,
                data={
                    "is_dm": bool(r.is_dm),
                    "project_id": r.project_id,
                    "parent_room_id": safe_parent,
                    "participant_count": participant_count_by_room.get(r.id, 0),
                    "representative_agent_id": safe_rep,
                    "is_typing": _is_typing_for(typing_tracker, r.id),
                },
            )
        )
    for p in projects_list:
        nodes.append(
            NodeOut(
                id=_pid(p.id),
                kind="project",
                label=p.name,
                data={"description": p.description},
            )
        )

    # Edges: the `_assemble_edges` helper filters by known id sets, so
    # any Participant row that points at a non-visible user/agent/room
    # is silently dropped — the visibility rule is therefore enforced
    # in a single place.
    edges = _assemble_edges(
        users=[me],
        machines=owned_machines,
        agents=all_agents,
        rooms=rooms_list,
        participants=[
            # Only surface participants the caller would see: ones that
            # map to agents in visible rooms + the caller's own rows.
            # Drop any that reference another user's id.
            p
            for p in room_participants
            if (p.user_id == user_id)
            or (p.agent_id is not None and p.agent_id in all_agent_ids)
        ],
        known_room_ids=known_room_ids,
    )

    return nodes, edges


def _assemble_edges(
    *,
    users: list[User],
    machines: list[Machine],
    agents: list[Agent],
    rooms: list[Room],
    participants: list[Participant],
    known_room_ids: set[str],
) -> list[EdgeOut]:
    """Assemble edges from the already-filtered row lists.

    Pure function — any visibility constraint must have been applied
    *before* the lists reach this helper.
    """
    edges: list[EdgeOut] = []
    edge_seq = 0

    def _next_id(prefix: str) -> str:
        nonlocal edge_seq
        edge_seq += 1
        return f"{prefix}{edge_seq}"

    known_user_ids = {u.id for u in users}
    known_machine_ids = {m.id for m in machines}
    known_agent_ids = {a.id for a in agents}

    # owns: user -> machine
    for m in machines:
        if m.owner_user_id in known_user_ids:
            edges.append(
                EdgeOut(
                    id=_next_id("eo"),
                    source=_uid(m.owner_user_id),
                    target=_mid(m.id),
                    kind="owns",
                )
            )

    # places: machine -> agent
    for a in agents:
        if a.placed_on_machine_id and a.placed_on_machine_id in known_machine_ids:
            edges.append(
                EdgeOut(
                    id=_next_id("ep"),
                    source=_mid(a.placed_on_machine_id),
                    target=_aid(a.id),
                    kind="places",
                )
            )

    # participates: user|agent -> room
    #
    # The agent-flavored edge folds in the "representative" relation via
    # ``data.is_representative``. This replaces the previous standalone
    # ``represents`` edge kind, which duplicated the same source/target
    # pair and forced the frontend to stack two overlapping lines on the
    # same agent→room connection (see #226).
    rep_by_room: dict[str, str] = {
        r.id: r.representative_agent_id
        for r in rooms
        if r.representative_agent_id
    }
    for p in participants:
        if p.room_id not in known_room_ids:
            continue
        if p.user_id is not None and p.user_id in known_user_ids:
            edges.append(
                EdgeOut(
                    id=_next_id("epu"),
                    source=_uid(p.user_id),
                    target=_rid(p.room_id),
                    kind="participates",
                    data={"actor": "user"},
                )
            )
        elif p.agent_id is not None and p.agent_id in known_agent_ids:
            is_representative = rep_by_room.get(p.room_id) == p.agent_id
            edges.append(
                EdgeOut(
                    id=_next_id("epa"),
                    source=_aid(p.agent_id),
                    target=_rid(p.room_id),
                    kind="participates",
                    data={"actor": "agent", "is_representative": is_representative},
                )
            )

    # parent_of: room -> child room. Skip if either end isn't visible,
    # and defensively skip trivial self-cycles.
    for r in rooms:
        if r.parent_room_id and r.parent_room_id in known_room_ids and r.parent_room_id != r.id:
            edges.append(
                EdgeOut(
                    id=_next_id("epf"),
                    source=_rid(r.parent_room_id),
                    target=_rid(r.id),
                    kind="parent_of",
                )
            )

    return edges


# ── ETag helper ─────────────────────────────────────────────────────


def _etag_for(graph: GraphOut) -> str:
    """Stable 16-char hex digest of the graph's nodes + edges.

    ``generated_at`` is intentionally EXCLUDED so two identical graphs
    generated seconds apart hash to the same value — that's the whole
    point of the ETag loop.
    """
    payload = {
        "scope": graph.scope,
        "nodes": sorted(
            (
                {"id": n.id, "kind": n.kind, "label": n.label, "data": n.data}
                for n in graph.nodes
            ),
            key=lambda x: x["id"],
        ),
        "edges": sorted(
            (
                {
                    "id": e.id,
                    "source": e.source,
                    "target": e.target,
                    "kind": e.kind,
                    "data": e.data,
                }
                for e in graph.edges
            ),
            key=lambda x: (x["source"], x["target"], x["kind"]),
        ),
    }
    blob = json.dumps(payload, default=str, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:16]
    return f'W/"{digest}"'


# ── Endpoint ────────────────────────────────────────────────────────


@router.get("", response_model=GraphOut)
async def get_graph(
    request: Request,
    response: Response,
    scope: ScopeKind = Query("auto"),
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Return a snapshot of the topology graph.

    Query params:
        scope: ``personal`` | ``global`` | ``auto`` (default).

    Admin users may request any scope; regular users are limited to
    ``personal`` (and ``auto`` resolves there). Guests are rejected by
    ``forbid_guest``.
    """
    if identity.kind != "user":
        # Agent-token callers don't have a topology view. They can see
        # their own rooms through the rooms API — the topology page is
        # a human-facing surface only.
        raise HTTPException(status_code=403, detail="User authentication required")

    resolved = _resolve_scope(identity, scope)
    # ``typing_tracker`` is attached during app startup
    # (``anygarden.app.create_app``); guard against missing state for test
    # apps that bypass the lifespan.
    typing_tracker = getattr(request.app.state, "typing_tracker", None)

    if resolved == "global":
        nodes, edges = await _build_global_graph(db, typing_tracker)
    else:
        nodes, edges = await _build_personal_graph(db, identity.id, typing_tracker)

    graph = GraphOut(
        generated_at=datetime.now(timezone.utc).isoformat(),
        scope=resolved,
        nodes=nodes,
        edges=edges,
    )

    etag = _etag_for(graph)
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match == etag:
        # 304 responses must not carry a body — return the Response
        # object directly so FastAPI doesn't try to serialize GraphOut.
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={
                "ETag": etag,
                "Cache-Control": "private, max-age=5",
            },
        )

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=5"
    return graph
