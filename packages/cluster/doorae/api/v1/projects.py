"""REST endpoints for Project management — ``/api/v1/projects``."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.db.models import Participant, Project, Room
from doorae.dependencies import forbid_guest, get_db

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ── Request / Response schemas ───────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    model_config = {"from_attributes": True}


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectOut)
async def create_project(
    body: ProjectCreate,
    # Projects are a registered-user concept — guests only see a
    # single room and should not discover the surrounding project
    # tree through this surface (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Create a new project."""
    project = Project(name=body.name, description=body.description)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    # Projects are a registered-user concept — guests only see a
    # single room and should not discover the surrounding project
    # tree through this surface (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """List all projects."""
    result = await db.execute(select(Project).order_by(Project.created_at))
    return list(result.scalars().all())


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    request: Request,
    # Same gate as create/list: registered users only. Project-level
    # owner/admin roles are not modelled separately today, so any
    # non-guest may delete any project — matching the existing write
    # surface on this resource.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Delete a project and every room it contains.

    ``Room.project_id`` is ``ON DELETE CASCADE`` (see
    ``db/models.py``) so the DB itself removes the child rooms,
    their participants and messages atomically with the project
    row. We still snapshot the audience BEFORE the commit so that
    ``RoomDeletedOut`` frames can be pushed to every affected user
    after the cascade — same pattern as
    ``rooms/router.py::delete_room``.
    """
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Audience capture before the cascade: room ids for per-room
    # broadcasts, plus the set of user ids who participate in any of
    # those rooms so we can reach each user's OTHER active WS too.
    room_ids = list(
        (
            await db.execute(select(Room.id).where(Room.project_id == project_id))
        ).scalars().all()
    )
    user_ids: set[str] = set()
    if room_ids:
        user_ids = {
            uid
            for uid in (
                await db.execute(
                    select(Participant.user_id).where(
                        Participant.room_id.in_(room_ids),
                        Participant.user_id.isnot(None),
                    )
                )
            ).scalars().all()
            if uid
        }

    await db.delete(project)
    await db.commit()

    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None and room_ids:
        # Lazy import — keeps the import graph flat, mirrors the
        # delete_room handler.
        from doorae.ws.protocol import RoomDeletedOut

        # 1) Anyone subscribed to a deleted room's WS at this instant
        #    gets the news on that channel. broadcast is best-effort
        #    and tolerant of already-closed sockets.
        for rid in room_ids:
            await manager.broadcast(rid, RoomDeletedOut(room_id=rid))

        # 2) Affected users watching a sibling (non-deleted) room
        #    need their sidebar invalidated too. Look up their
        #    still-live participant ids (the cascade just removed the
        #    ones inside the deleted rooms) and push one frame per
        #    deleted room to each — the frontend's room_deleted
        #    handler reconciles the tree.
        if user_ids:
            other_pids = (
                await db.execute(
                    select(Participant.id).where(
                        Participant.user_id.in_(user_ids)
                    )
                )
            ).scalars().all()
            for pid in other_pids:
                for rid in room_ids:
                    await manager.send_to(pid, RoomDeletedOut(room_id=rid))

    return None
