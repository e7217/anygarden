"""Administrator room-memory edits and explicit legacy archive import."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, Room
from anygarden.dependencies import get_admin_identity, get_db
from anygarden.memory.service import (
    get_room_memory,
    push_room_memory,
    update_room_memory,
)
from anygarden.rooms.authorization import Capability, require_capability

router = APIRouter(prefix="/api/v1/agents", tags=["room memory"])


class MemoryRevision(BaseModel):
    expected_revision: int = Field(ge=0, strict=True)
    model_config = {"extra": "forbid"}


class MemoryEdit(MemoryRevision):
    memory_md: str = Field(max_length=262144)


async def _authorize(db: AsyncSession, identity: Identity, agent_id: str, room_id: str, *, write: bool) -> None:
    await require_capability(
        db, room_id=room_id, identity=identity,
        capability=Capability.FILE_MANAGE if write else Capability.FILE_READ,
    )
    # get_room_memory additionally requires this agent's active room membership.
    await get_room_memory(db, agent_id, room_id)


async def _out(db: AsyncSession, agent_id: str, room_id: str, snapshot: dict) -> dict:
    agent = await db.get(Agent, agent_id)
    room = await db.get(Room, room_id)
    return {
        **snapshot,
        "agent_id": agent_id,
        "room_name": room.name,
        "project_id": room.project_id,
        "legacy_archive_available": bool(agent.memory_md),
    }


@router.get("/{agent_id}/rooms/{room_id}/memory")
async def read_memory(
    agent_id: str,
    room_id: str,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _authorize(db, identity, agent_id, room_id, write=False)
    return await _out(db, agent_id, room_id, await get_room_memory(db, agent_id, room_id))


async def _save(
    request: Request, db: AsyncSession, *, agent_id: str, room_id: str,
    expected_revision: int, memory_md: str,
) -> dict:
    snapshot = await update_room_memory(
        db, agent_id=agent_id, room_id=room_id, memory_md=memory_md,
        expected_revision=expected_revision, administrator=True,
    )
    response = await _out(db, agent_id, room_id, snapshot)
    await db.commit()
    await push_room_memory(request.app, db, agent_id=agent_id, room_id=room_id, machine_snapshot=True)
    return response


@router.put("/{agent_id}/rooms/{room_id}/memory")
async def edit_memory(
    agent_id: str,
    room_id: str,
    body: MemoryEdit,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _authorize(db, identity, agent_id, room_id, write=True)
    return await _save(request, db, agent_id=agent_id, room_id=room_id,
                       expected_revision=body.expected_revision, memory_md=body.memory_md)


@router.post("/{agent_id}/rooms/{room_id}/memory/clear")
async def clear_memory(
    agent_id: str,
    room_id: str,
    body: MemoryRevision,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _authorize(db, identity, agent_id, room_id, write=True)
    return await _save(request, db, agent_id=agent_id, room_id=room_id,
                       expected_revision=body.expected_revision, memory_md="")


@router.post("/{agent_id}/rooms/{room_id}/memory/import-legacy")
async def import_legacy_memory(
    agent_id: str,
    room_id: str,
    body: MemoryRevision,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _authorize(db, identity, agent_id, room_id, write=True)
    agent = await db.get(Agent, agent_id)
    if not agent.memory_md:
        raise HTTPException(409, {"code": "MEMORY_ARCHIVE_EMPTY", "message": "Legacy archive is empty"})
    return await _save(request, db, agent_id=agent_id, room_id=room_id,
                       expected_revision=body.expected_revision, memory_md=agent.memory_md)
