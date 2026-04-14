"""REST endpoints for per-room task management."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.db.models import Room, Task
from doorae.dependencies import get_current_identity, get_db

router = APIRouter(tags=["tasks"])


class TaskCreate(BaseModel):
    title: str
    status: str = "todo"
    assignee_participant_id: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    assignee_participant_id: Optional[str] = None


class TaskOut(BaseModel):
    id: str
    room_id: str
    title: str
    status: str
    assignee_participant_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str

    model_config = {"from_attributes": True}


@router.post("/api/v1/rooms/{room_id}/tasks", status_code=201, response_model=TaskOut)
async def create_task(
    room_id: str,
    body: TaskCreate,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Create a task in a room."""
    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    task = Task(
        room_id=room_id,
        title=body.title,
        status=body.status,
        assignee_participant_id=body.assignee_participant_id,
        created_by=identity.id if identity.kind == "user" else None,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return TaskOut(
        id=task.id, room_id=task.room_id, title=task.title,
        status=task.status, assignee_participant_id=task.assignee_participant_id,
        created_by=task.created_by, created_at=task.created_at.isoformat(),
    )


@router.get("/api/v1/rooms/{room_id}/tasks", response_model=list[TaskOut])
async def list_tasks(
    room_id: str,
    status: Optional[str] = None,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """List tasks in a room, optionally filtered by status."""
    stmt = select(Task).where(Task.room_id == room_id)
    if status:
        stmt = stmt.where(Task.status == status)
    stmt = stmt.order_by(Task.created_at)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        TaskOut(
            id=t.id, room_id=t.room_id, title=t.title,
            status=t.status, assignee_participant_id=t.assignee_participant_id,
            created_by=t.created_by, created_at=t.created_at.isoformat(),
        )
        for t in rows
    ]


@router.put("/api/v1/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: str,
    body: TaskUpdate,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Update a task's title, status, or assignee."""
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.title is not None:
        task.title = body.title
    if body.status is not None:
        task.status = body.status
    if body.assignee_participant_id is not None:
        task.assignee_participant_id = body.assignee_participant_id

    await db.commit()
    await db.refresh(task)
    return TaskOut(
        id=task.id, room_id=task.room_id, title=task.title,
        status=task.status, assignee_participant_id=task.assignee_participant_id,
        created_by=task.created_by, created_at=task.created_at.isoformat(),
    )


@router.delete("/api/v1/tasks/{task_id}", status_code=200)
async def delete_task(
    task_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Delete a task."""
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.commit()
    return {"deleted": True}
