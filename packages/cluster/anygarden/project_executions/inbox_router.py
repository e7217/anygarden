"""All accessible project notifications; independent of selected room."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.dependencies import get_current_identity, get_db
from anygarden.project_executions.inbox import list_inbox

router = APIRouter(tags=["project inbox"])


@router.get("/api/v1/inbox")
async def get_inbox(
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    config = getattr(request.app.state, "config", None)
    return await list_inbox(db, identity=identity, targets=getattr(config, "project_action_targets", {}))
