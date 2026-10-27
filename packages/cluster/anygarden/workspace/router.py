"""REST for workspace action approvals (#938)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
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
    return {**approvals.view(row), "can_decide": row.status == "pending" and approvals.may_decide(row, identity)}


@router.post("/{request_id}/approve")
async def approve_workspace_action(
    request_id: str, request: Request, identity: Identity = Depends(forbid_guest),
) -> dict:
    try:
        row = await approvals.approve(request.app.state.session_factory, request,
                                      request_id=request_id, identity=identity)
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
