"""Read the workspace as a person sees it (#938).

``workspace_query`` answers with what the person whose message started the
turn could see in the web UI, trimmed to the fields an agent needs to
decide and to name things in a follow-up ``workspace_action``.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.workspace.actions import (
    ActionFailed,
    _brief,
    _detail,
    require_visible_room,
)

QUERY_KINDS = ("projects", "rooms", "room", "agents", "machines", "tasks", "routines", "goals")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _page(kind: str, items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    return {"kind": kind, "items": items[:limit], "truncated": len(items) > limit}


def _need_room(room_id: str | None, kind: str) -> str:
    if not room_id:
        raise ActionFailed("INVALID_PARAMS", f"kind={kind} needs room_id")
    return room_id


async def run_query(
    db: AsyncSession,
    request: Request,
    identity: Identity,
    *,
    kind: str,
    room_id: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Raises :class:`ActionFailed` for a bad kind, a hidden room or a refused read."""

    try:
        limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    except (TypeError, ValueError):
        raise ActionFailed("INVALID_PARAMS", "limit must be a whole number") from None
    if kind not in QUERY_KINDS:
        raise ActionFailed("INVALID_PARAMS", f"kind must be one of: {', '.join(QUERY_KINDS)}")
    try:
        return await _QUERIES[kind](db, request, identity, room_id, status, limit)
    except HTTPException as exc:
        raise ActionFailed(f"HTTP_{exc.status_code}", _detail(exc)) from None


async def _projects(db, request, identity, room_id, status, limit):
    from anygarden.api.v1.projects import list_projects

    rows = await list_projects(identity=identity, db=db)
    return _page("projects", [_brief(r, "id", "name", "description") for r in rows], limit)


async def _rooms(db, request, identity, room_id, status, limit):
    from anygarden.rooms.router import list_rooms

    rows = await list_rooms(request=request, project_id=None, is_dm=False, representative_agent_id=None,
                            identity=identity, db=db)
    items = [_brief(r, "id", "name", "project_id", "parent_room_id", "representative_agent_id") for r in rows]
    return _page("rooms", items, limit)


async def _room(db, request, identity, room_id, status, limit):
    from anygarden.rooms.router import get_room

    room_id = _need_room(room_id, "room")
    detail = await get_room(room_id=room_id, request=request, identity=identity, db=db)
    data = detail.model_dump(mode="json")
    participants = [
        {k: p.get(k) for k in ("id", "display_name", "kind", "agent_id", "user_id", "role")}
        for p in data.get("participants") or []
    ]
    room = {k: data.get(k) for k in ("id", "name", "description", "project_id", "parent_room_id",
                                     "representative_agent_id", "archived_at")}
    return {"kind": "room", "room": room, "participants": participants[:limit],
            "truncated": len(participants) > limit}


async def _agents(db, request, identity, room_id, status, limit):
    from anygarden.db.models import Agent, Participant
    from anygarden.workspace.actions import _visible_rooms

    stmt = select(Agent).order_by(Agent.name)
    if not (identity.claims and getattr(identity.claims, "is_admin", False)):
        # A non-admin sees the agents of rooms they can see, as in a room's member list.
        visible = await _visible_rooms(db, request, identity)
        stmt = stmt.where(Agent.id.in_(
            select(Participant.agent_id).where(Participant.room_id.in_(visible), Participant.agent_id.isnot(None))
        ))
    rows = list(await db.scalars(stmt))
    items = [_brief(a, "id", "name", "engine", "description", "desired_state", "actual_state",
                    "unavailable_code", "permission_level") for a in rows]
    return _page("agents", items, limit)


async def _machines(db, request, identity, room_id, status, limit):
    from anygarden.api.v1.machines import list_machines

    rows = await list_machines(identity=identity, db=db)
    return _page("machines", [_brief(m, "id", "name", "status", "last_seen_at") for m in rows], limit)


async def _tasks(db, request, identity, room_id, status, limit):
    from anygarden.api.v1.tasks import list_tasks

    rows = await list_tasks(room_id=_need_room(room_id, "tasks"), status=status, goal_id=None,
                            identity=identity, db=db)
    items = [_brief(t, "id", "title", "status", "assignee_participant_id", "goal_id", "created_at") for t in rows]
    return _page("tasks", items, limit)


async def _routines(db, request, identity, room_id, status, limit):
    from anygarden.api.v1.goals import list_goals_for_room

    room_id = _need_room(room_id, "routines")
    # The route itself does not check room access.
    await require_visible_room(db, request, identity, room_id)
    rows = await list_goals_for_room(room_id=room_id, identity=identity, db=db)
    items = [_brief(g, "id", "title", "status", "assignee_agent_id", "trigger_type", "next_run_at")
             for g in rows if status is None or g.status == status]
    return _page("routines", items, limit)


async def _goals(db, request, identity, room_id, status, limit):
    from anygarden.api.v1.campaigns import list_room_campaigns

    rows = await list_room_campaigns(room_id=_need_room(room_id, "goals"), status=status,
                                     identity=identity, db=db)
    return _page("goals", [_brief(c, "id", "title", "status", "mode") for c in rows], limit)


_QUERIES = {
    "projects": _projects,
    "rooms": _rooms,
    "room": _room,
    "agents": _agents,
    "machines": _machines,
    "tasks": _tasks,
    "routines": _routines,
    "goals": _goals,
}
