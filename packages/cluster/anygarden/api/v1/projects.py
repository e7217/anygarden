"""REST endpoints for Project management — ``/api/v1/projects``."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Participant, Project, Room, UserProjectOrder
from anygarden.dependencies import forbid_guest, get_db
from anygarden.rooms.authorization import is_global_admin
from anygarden.rooms.service import PIN_ORDER_STEP

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ── Request / Response schemas ───────────────────────────────────────


class ProjectCreate(BaseModel):
    # #471 — reject empty/oversized names at the edge (422). 255 mirrors
    # the ``Project.name String(255)`` column; sibling create schemas
    # (goals.py, auth/routes.py) already enforce the same min_length=1.
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_by: Optional[str] = None
    # #783 — lets the UI hide actions the caller is not allowed to take.
    can_delete: bool = False


class ProjectOrderBody(BaseModel):
    # Full snapshot of the caller's sidebar order (#995).
    project_ids: list[str] = Field(max_length=1000)


class ProjectOrderOut(BaseModel):
    project_ids: list[str]


# ── Access rules (#783) ──────────────────────────────────────────────
#
# A project is visible to a global admin, to its creator, and to anyone
# who participates in one of its rooms. Only the creator or a global
# admin may delete it; projects created before migration 082 have no
# recorded creator and are deletable by admins only.


def _member_project_ids(identity: Identity):
    """Subquery of project ids where *identity* participates in a room."""
    member = (
        Participant.agent_id == identity.id
        if identity.kind == "agent"
        else Participant.user_id == identity.id
    )
    return (
        select(Room.project_id)
        .join(Participant, Participant.room_id == Room.id)
        .where(member, Room.project_id.isnot(None))
    )


def _can_delete(project: Project, identity: Identity) -> bool:
    if is_global_admin(identity):
        return True
    return identity.kind == "user" and project.created_by == identity.id


def _out(project: Project, identity: Identity) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        created_by=project.created_by,
        can_delete=_can_delete(project, identity),
    )


async def _visible_projects(db: AsyncSession, identity: Identity) -> list[Project]:
    """Projects *identity* may see, in the caller's sidebar order (#995)."""
    stmt = select(Project).outerjoin(
        UserProjectOrder,
        (UserProjectOrder.project_id == Project.id)
        & (UserProjectOrder.user_id == identity.id),
    )
    if not is_global_admin(identity):
        stmt = stmt.where(
            or_(
                Project.created_by == identity.id,
                Project.id.in_(_member_project_ids(identity)),
            )
        )
    stmt = stmt.order_by(
        case((UserProjectOrder.sort_order.is_(None), 1), else_=0),
        UserProjectOrder.sort_order,
        Project.created_at,
    )
    return list((await db.execute(stmt)).scalars().all())


async def _send_order_to_user(
    request: Request, db: AsyncSession, *, user_id: str, project_ids: list[str]
) -> None:
    """Push the new order to every live WS session of *user_id*.

    Same fan-out as the pinned-room order: the connection manager is
    keyed by participant id, so resolve the user's participant rows.
    """
    manager = getattr(request.app.state, "connection_manager", None)
    if manager is None:
        return
    from anygarden.ws.protocol import ProjectOrderChangedOut

    frame = ProjectOrderChangedOut(user_id=user_id, project_ids=project_ids)
    participant_ids = (
        await db.execute(select(Participant.id).where(Participant.user_id == user_id))
    ).scalars().all()
    for pid in participant_ids:
        await manager.send_to(pid, frame)


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
    """Create a new project owned by the calling user."""
    project = Project(
        name=body.name,
        description=body.description,
        created_by=identity.id if identity.kind == "user" else None,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return _out(project, identity)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    # Projects are a registered-user concept — guests only see a
    # single room and should not discover the surrounding project
    # tree through this surface (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """List the projects the caller may see (see access rules above).

    Projects the caller has placed (#995) come first in their order;
    the rest follow by creation time.
    """
    return [_out(p, identity) for p in await _visible_projects(db, identity)]


@router.put("/order", response_model=ProjectOrderOut)
async def set_project_order(
    body: ProjectOrderBody,
    request: Request,
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Rewrite the caller's sidebar project order from a full snapshot (#995).

    Ids the caller cannot see are dropped silently so the call does not
    reveal whether a project exists. Saved positions of projects missing
    from the snapshot are kept, so a partial payload never loses order.
    """
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="Only users can reorder projects")
    visible = {p.id for p in await _visible_projects(db, identity)}
    existing = {
        row.project_id: row
        for row in (
            await db.execute(
                select(UserProjectOrder).where(UserProjectOrder.user_id == identity.id)
            )
        ).scalars().all()
    }
    position = 0
    for project_id in dict.fromkeys(body.project_ids):
        if project_id not in visible:
            continue
        position += 1
        row = existing.get(project_id)
        if row is None:
            row = UserProjectOrder(user_id=identity.id, project_id=project_id)
            db.add(row)
        row.sort_order = position * PIN_ORDER_STEP
    await db.commit()

    order = [p.id for p in await _visible_projects(db, identity)]
    await _send_order_to_user(request, db, user_id=identity.id, project_ids=order)
    return ProjectOrderOut(project_ids=order)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    request: Request,
    # Guests never reach projects; the creator/admin rule below (#783)
    # decides for everyone else.
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
    if not _can_delete(project, identity):
        # #783 — callers who cannot see the project get the same 404 as
        # for a missing one, so deletion attempts don't reveal it exists.
        is_member = (
            await db.execute(
                select(Project.id).where(
                    Project.id == project_id,
                    Project.id.in_(_member_project_ids(identity)),
                )
            )
        ).scalar_one_or_none()
        if is_member is None:
            raise HTTPException(status_code=404, detail="Project not found")
        raise HTTPException(
            status_code=403,
            detail="Only the project creator or an admin can delete this project",
        )

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
        from anygarden.ws.protocol import RoomDeletedOut

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
