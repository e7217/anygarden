"""REST endpoints for Project management — ``/api/v1/projects``."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.db.models import Project
from doorae.dependencies import get_current_identity, get_db

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
    identity: Identity = Depends(get_current_identity),
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
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """List all projects."""
    result = await db.execute(select(Project).order_by(Project.created_at))
    return list(result.scalars().all())
