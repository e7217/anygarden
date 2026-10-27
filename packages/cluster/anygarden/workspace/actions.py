"""Run one workspace action with a person's rights (#938).

Each runner calls the REST route function for that change, passing the
person's :class:`Identity`, so the permission checks, side effects and WS
fan-out are exactly the ones the web UI gets. Route ``Depends`` guards do
not run on a direct call; ``admin_only`` in the policy table and the
room-visibility check here stand in for them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.workspace import policy as p


class ActionFailed(ValueError):
    """The change was refused or failed; ``detail`` is safe to show."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or detail)
    return str(detail)


async def _visible_rooms(db: AsyncSession, request: Request, identity: Identity) -> frozenset[str]:
    from anygarden.rooms.authorization import accessible_room_ids

    return await accessible_room_ids(
        db, identity=identity, channel_service=getattr(request.app.state, "channel_service", None),
    )


async def require_visible_room(db: AsyncSession, request: Request, identity: Identity, room_id: str) -> None:
    if room_id not in await _visible_rooms(db, request, identity):
        raise ActionFailed("ROOM_NOT_FOUND", f"Room {room_id} not found")


def _brief(obj: Any, *fields: str) -> dict[str, Any]:
    data = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else {f: getattr(obj, f, None) for f in fields}
    return {f: data.get(f) for f in fields}


async def _create_room(db, request, identity, params: p.CreateRoomParams, ctx) -> dict:
    from anygarden.db.models import Participant
    from anygarden.rooms.router import (
        RoomCreate,
        SubRoomCreate,
        create_room,
        create_sub_room_endpoint,
    )

    if params.parent_room_id is not None:
        await require_visible_room(db, request, identity, params.parent_room_id)
        creator = await db.scalar(
            select(Participant.id).where(
                Participant.room_id == params.parent_room_id, Participant.user_id == identity.id
            )
        )
        if creator is None:
            raise ActionFailed("NOT_A_MEMBER", "The person must be a member of the parent room")
        room = await create_sub_room_endpoint(
            room_id=params.parent_room_id,
            body=SubRoomCreate(
                name=params.name, description=params.description,
                participants=params.member_participant_ids, creator_participant_id=creator,
            ),
            request=request, identity=identity, db=db,
        )
    else:
        room = await create_room(
            body=RoomCreate(project_id=params.project_id, name=params.name, description=params.description),
            identity=identity, db=db,
        )
    return _brief(room, "id", "name", "parent_room_id", "project_id")


async def _add_room_member(db, request, identity, params: p.AddRoomMemberParams, ctx) -> dict:
    from anygarden.rooms.router import ParticipantAdd, add_participant

    background = BackgroundTasks()
    out = await add_participant(
        room_id=params.room_id, body=ParticipantAdd(agent_id=params.agent_id),
        request=request, background=background, identity=identity, db=db,
    )
    await background()
    return _brief(out, "id", "display_name", "agent_id", "room_id")


async def _create_task(db, request, identity, params: p.CreateTaskParams, ctx) -> dict:
    from anygarden.api.v1.tasks import TaskCreate, create_task

    out = await create_task(
        room_id=params.room_id,
        body=TaskCreate(title=params.title, spec=params.spec, assignee_participant_id=params.assignee_participant_id),
        request=request, identity=identity, db=db,
    )
    return _brief(out, "id", "room_id", "title", "status", "assignee_participant_id")


async def _reassign_task(db, request, identity, params: p.ReassignTaskParams, ctx) -> dict:
    from anygarden.api.v1.tasks import TaskUpdate, update_task

    out = await update_task(
        task_id=params.task_id, body=TaskUpdate(assignee_participant_id=params.assignee_participant_id),
        request=request, identity=identity, db=db,
    )
    return _brief(out, "id", "room_id", "title", "status", "assignee_participant_id")


def _routine_brief(out) -> dict:
    return _brief(out, "id", "title", "status", "assignee_agent_id", "report_room_id", "next_run_at")


async def _pause_routine(db, request, identity, params: p.RoutineRefParams, ctx) -> dict:
    from anygarden.api.v1.goals import pause_goal

    return _routine_brief(await pause_goal(goal_id=params.routine_id, identity=identity, db=db))


async def _resume_routine(db, request, identity, params: p.RoutineRefParams, ctx) -> dict:
    from anygarden.api.v1.goals import resume_goal

    return _routine_brief(await resume_goal(goal_id=params.routine_id, identity=identity, db=db))


def _goal_brief(out) -> dict:
    return _brief(out, "id", "room_id", "title", "status", "mode")


async def _pause_goal(db, request, identity, params: p.GoalRefParams, ctx) -> dict:
    from anygarden.api.v1.campaigns import pause_campaign

    return _goal_brief(await pause_campaign(campaign_id=params.goal_id, request=request, identity=identity, db=db))


async def _resume_goal(db, request, identity, params: p.GoalRefParams, ctx) -> dict:
    from anygarden.api.v1.campaigns import resume_campaign

    return _goal_brief(await resume_campaign(campaign_id=params.goal_id, request=request, identity=identity, db=db))


async def _create_routine(db, request, identity, params: p.CreateRoutineParams, ctx) -> dict:
    from anygarden.api.v1.goals import GoalCreate, create_goal

    # The route does not check that the person can see the report room.
    await require_visible_room(db, request, identity, params.report_room_id)
    out = await create_goal(
        agent_id=params.agent_id,
        body=GoalCreate(
            title=params.title, spec=params.spec, trigger_type=params.trigger_type,
            trigger_config=params.trigger_config, materialize=params.materialize,
            report_room_id=params.report_room_id,
        ),
        request=request, identity=identity, db=db,
    )
    return _routine_brief(out)


async def _create_goal(db, request, identity, params: p.CreateGoalParams, ctx) -> dict:
    from anygarden.api.v1.campaigns import CampaignCreate, create_campaign

    out = await create_campaign(
        room_id=params.room_id, body=CampaignCreate.model_validate(params.spec),
        request=request, identity=identity, db=db,
    )
    return _goal_brief(out)


async def _archive_room(db, request, identity, params: p.ArchiveRoomParams, ctx) -> dict:
    from anygarden.rooms.router import archive_room

    return _brief(await archive_room(room_id=params.room_id, request=request, identity=identity, db=db),
                  "id", "name", "archived_at")


async def _hire_agent(db, request, identity, params: p.HireAgentParams, ctx) -> dict:
    from anygarden.api.v1.agents import AgentCreate, create_agent
    from anygarden.db.models import Agent

    creator = await db.get(Agent, ctx["agent_id"])
    if creator is None or not creator.can_create_agents:
        raise ActionFailed("HIRE_NOT_ALLOWED", "This agent may not propose new agents")
    for room_id in params.room_ids:
        await require_visible_room(db, request, identity, room_id)
    out = await create_agent(
        body=AgentCreate(
            engine=params.engine, name=params.name, machine_id=params.machine_id, rooms=params.room_ids,
            agents_md=params.agents_md, description=params.description,
            permission_level=p.capped_permission(params.permission_level, creator.permission_level),
        ),
        request=request, identity=identity, db=db,
    )
    return _brief(out, "id", "name", "engine", "permission_level", "desired_state")


Runner = Callable[[AsyncSession, Request, Identity, Any, dict], Awaitable[dict]]

RUNNERS: dict[str, Runner] = {
    "create_room": _create_room,
    "add_room_member": _add_room_member,
    "create_task": _create_task,
    "reassign_task": _reassign_task,
    "pause_routine": _pause_routine,
    "resume_routine": _resume_routine,
    "pause_goal": _pause_goal,
    "resume_goal": _resume_goal,
    "create_routine": _create_routine,
    "create_goal": _create_goal,
    "archive_room": _archive_room,
    "hire_agent": _hire_agent,
}


def parse_params(action: str, raw: object) -> BaseModel:
    """Validate *raw* for *action*. Raises :class:`ActionFailed`."""

    spec = p.ACTIONS.get(action)
    if spec is None or spec.tier == "off":
        raise ActionFailed("UNKNOWN_ACTION", f"Unknown action {action!r}; one of: {', '.join(sorted(p.ACTIONS))}")
    try:
        return spec.params.model_validate(raw if raw is not None else {})
    except ValidationError as exc:
        raise ActionFailed("INVALID_PARAMS", _validation_text(exc)) from None


def _validation_text(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        where = ".".join(str(x) for x in err.get("loc", ())) or "params"
        parts.append(f"{where}: {err.get('msg')}")
    return "; ".join(parts)


async def run_action(
    db: AsyncSession, request: Request, identity: Identity, action: str, params: BaseModel, *, agent_id: str,
) -> dict:
    """Run *action* as *identity*. Raises :class:`ActionFailed`."""

    spec = p.ACTIONS[action]
    if spec.admin_only and not (identity.claims and getattr(identity.claims, "is_admin", False)):
        raise ActionFailed("ADMIN_REQUIRED", "Only an admin can do this")
    try:
        return await RUNNERS[action](db, request, identity, params, {"agent_id": agent_id})
    except HTTPException as exc:
        await db.rollback()
        raise ActionFailed(f"HTTP_{exc.status_code}", _detail(exc)) from None
    except ValidationError as exc:
        await db.rollback()
        raise ActionFailed("INVALID_PARAMS", _validation_text(exc)) from None
