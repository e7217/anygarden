"""Admin-only browsing of runtime-reported files on the execution machine."""

from __future__ import annotations

from typing import Annotated, Literal

from anygarden_machine.managed_workspace import (
    CAPABILITY,
    WorkspaceSnapshot,
    path_parts,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, Machine
from anygarden.dependencies import get_admin_identity, get_db
from anygarden.scheduler.machine_bus import MachineRequestError

router = APIRouter()


class ManagedWorkspaceOut(BaseModel):
    status: str
    machine_id: str | None = None
    machine_name: str | None = None
    agent_state: str
    snapshot: WorkspaceSnapshot | None = None


async def _query(
    request: Request,
    db: AsyncSession,
    agent_id: str,
    operation: Literal["list", "read"],
    path: str,
    cursor: str | None,
) -> ManagedWorkspaceOut:
    try:
        parts = path_parts(path)
        if operation == "read" and not parts:
            raise ValueError("file required")
        if cursor is not None and (
            not cursor
            or "/" in cursor
            or "\\" in cursor
            or any(ord(c) < 32 for c in cursor)
        ):
            raise ValueError("invalid cursor")
    except ValueError as exc:
        raise HTTPException(422, "Invalid workspace path") from exc
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    machine_id = agent.placed_on_machine_id
    generation = agent.generation
    result = ManagedWorkspaceOut(status="not_placed", agent_state=agent.actual_state)
    if machine_id is None:
        return result
    machine = await db.get(Machine, machine_id)
    result.machine_id = machine_id
    result.machine_name = machine.name if machine else None
    bus = request.app.state.machine_bus
    if machine is None or not bus.is_connected(machine_id):
        result.status = "offline"
        return result
    if CAPABILITY not in (machine.control_capabilities or []):
        result.status = "unsupported"
        return result
    # Release the read transaction while waiting on another machine. A fresh
    # read below must observe placement/generation changes during that wait.
    await db.rollback()
    try:
        raw = await bus.request_workspace(
            machine_id,
            agent_id=agent_id,
            generation=generation,
            operation=operation,
            path=path,
            cursor=cursor,
        )
    except MachineRequestError as exc:
        result.status = exc.reason
        return result
    current = (
        await db.execute(
            select(
                Agent.placed_on_machine_id,
                Agent.generation,
                Agent.actual_state,
            ).where(Agent.id == agent_id)
        )
    ).first()
    if current is None or (current.placed_on_machine_id, current.generation) != (
        machine_id,
        generation,
    ):
        raise HTTPException(409, "Workspace changed. Refresh and try again.")
    result.agent_state = current.actual_state
    result.snapshot = WorkspaceSnapshot.model_validate(raw)
    result.status = result.snapshot.status
    return result


@router.get("/{agent_id}/workspace", response_model=ManagedWorkspaceOut)
async def list_managed_workspace(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
    path: Annotated[str, Query(max_length=1024)] = "",
    cursor: Annotated[str | None, Query(max_length=255)] = None,
):
    return await _query(request, db, agent_id, "list", path, cursor)


@router.get("/{agent_id}/workspace/file", response_model=ManagedWorkspaceOut)
async def read_managed_workspace(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
    path: Annotated[str, Query(min_length=1, max_length=1024)],
):
    return await _query(request, db, agent_id, "read", path, None)
