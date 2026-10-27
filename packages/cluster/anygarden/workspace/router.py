"""REST for workspace action approvals (#938)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.workspace_action_models import WorkspaceActionRequest
from anygarden.dependencies import forbid_guest, get_db
from anygarden.workspace import approvals
from anygarden.workspace.actions import ActionFailed, require_visible_room

router = APIRouter(prefix="/api/v1/workspace-actions", tags=["workspace-actions"])


def _refused(exc: approvals.DecisionRefused) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.detail})


@router.get("/{request_id}")
async def get_workspace_action(
    request_id: str,
    request: Request,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.get(WorkspaceActionRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    try:
        await require_visible_room(db, request, identity, row.room_id)
    except ActionFailed:
        raise HTTPException(status_code=404, detail="Request not found") from None
    await approvals.expire_if_due(db, row)
    return {**approvals.view(row), "can_decide": row.status == "pending" and await approvals.may_approve(db, row, identity)}


class ApproveIn(BaseModel):
    """#947/#948 — a goal's or routine's values as edited in the form."""

    model_config = ConfigDict(extra="forbid")
    params: dict | None = None


@router.post("/{request_id}/approve")
async def approve_workspace_action(
    request_id: str, request: Request, body: ApproveIn | None = None, identity: Identity = Depends(forbid_guest),
) -> dict:
    try:
        row = await approvals.approve(request.app.state.session_factory, request,
                                      request_id=request_id, identity=identity,
                                      params=body.params if body else None)
    except approvals.DecisionRefused as exc:
        raise _refused(exc) from None
    return {**approvals.view(row), "can_decide": False}


@router.post("/{request_id}/reject")
async def reject_workspace_action(
    request_id: str, request: Request, identity: Identity = Depends(forbid_guest),
) -> dict:
    try:
        row = await approvals.reject(request.app.state.session_factory, request,
                                     request_id=request_id, identity=identity)
    except approvals.DecisionRefused as exc:
        raise _refused(exc) from None
    return {**approvals.view(row), "can_decide": False}


drafts_router = APIRouter(prefix="/api/v1/rooms", tags=["workspace-actions"])


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["goal", "routine"]
    description: str = Field(min_length=1, max_length=4000)
    agent_id: str | None = None
    """``/routine @agent`` — the agent that drafts and runs it."""
    timezone: str | None = Field(default=None, max_length=64)
    """The person's IANA timezone, for a routine's schedule."""


@drafts_router.post("/{room_id}/drafts", status_code=201)
async def request_draft(
    room_id: str, body: DraftIn, request: Request, identity: Identity = Depends(forbid_guest),
) -> dict:
    """Ask an agent to draft a goal or routine (#947, #948)."""

    from anygarden.db.models import Room, User
    from anygarden.mcp.project_tools import broadcast_project_messages
    from anygarden.rooms.authorization import Capability, require_capability
    from anygarden.workspace import drafts

    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="user identity required")
    session_factory = request.app.state.session_factory
    async with session_factory() as db:
        if body.kind == "goal":
            # Same gate as starting a goal from the form.
            await require_capability(db, room_id=room_id, identity=identity, capability=Capability.TASK_MANAGE)
        else:
            try:
                await require_visible_room(db, request, identity, room_id)
            except ActionFailed:
                raise HTTPException(status_code=404, detail="Room not found") from None
        room = await db.get(Room, room_id)
        user = await db.get(User, identity.id)
        if room is None or user is None:
            raise HTTPException(status_code=404, detail="Room not found")
        try:
            draft, message = await drafts.open_draft(
                db, room=room, user=user, kind=body.kind, description=body.description,
                agent_id=body.agent_id if body.kind == "routine" else None, timezone=body.timezone,
            )
        except drafts.DraftRefused as exc:
            await db.rollback()
            raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.detail}) from None
        result = drafts.view(draft)
        await db.commit()
        await broadcast_project_messages(db, request=request, messages=[message])
    return result
