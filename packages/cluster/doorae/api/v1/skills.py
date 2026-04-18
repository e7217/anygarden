"""REST endpoints for SkillLibrary — ``/api/v1/admin/skills`` (#119 Phase 1).

All endpoints require admin identity (``get_admin_identity`` dependency).
The actual SkillLibraryService instance lives on ``app.state`` so tests
can wire a fake fetcher without monkey-patching.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.db.models import Agent, AgentSkill, SkillLibraryEntry
from doorae.dependencies import get_admin_identity, get_db
from doorae.skills_library.service import SkillLibraryService

router = APIRouter(prefix="/api/v1/admin/skills", tags=["skills"])


# ── Request / Response schemas ───────────────────────────────────────


class SkillRegisterRequest(BaseModel):
    source: str
    name: str
    rev: str = "HEAD"


class SkillAttachRequest(BaseModel):
    agent_id: str


class SkillOut(BaseModel):
    id: str
    source: str
    name: str
    pinned_rev: str
    scripts_detected: list[str]
    content_hash: str
    approved_by: Optional[str]
    fetched_at: datetime
    # List of agent IDs currently attached. Kept out of
    # SkillLibraryEntry.model_config (``from_attributes``) because
    # it requires a join — the handler fills this in explicitly.
    attached_agent_ids: list[str] = []
    model_config = {"from_attributes": True}


# ── Helpers ──────────────────────────────────────────────────────────


def _service(request: Request) -> SkillLibraryService:
    service = getattr(request.app.state, "skill_library_service", None)
    if service is None:
        # This is a server-side configuration bug, not a caller error;
        # surface it as 500 so ops catches it during smoke tests.
        raise HTTPException(
            status_code=500,
            detail="skill_library_service not configured on app.state",
        )
    return service


async def _serialize(
    entry: SkillLibraryEntry,
    db: AsyncSession,
) -> SkillOut:
    """Attach ``attached_agent_ids`` onto an ORM row."""
    agent_ids = (
        await db.execute(
            select(AgentSkill.agent_id).where(
                AgentSkill.skill_library_id == entry.id
            )
        )
    ).scalars().all()
    return SkillOut(
        id=entry.id,
        source=entry.source,
        name=entry.name,
        pinned_rev=entry.pinned_rev,
        scripts_detected=list(entry.scripts_detected or []),
        content_hash=entry.content_hash,
        approved_by=entry.approved_by,
        fetched_at=entry.fetched_at,
        attached_agent_ids=list(agent_ids),
    )


# ── Endpoints ───────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=SkillOut)
async def register_skill(
    body: SkillRegisterRequest,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> SkillOut:
    service = _service(request)
    entry = await service.register(
        source=body.source,
        name=body.name,
        rev=body.rev,
    )
    return await _serialize(entry, db)


@router.get("", response_model=list[SkillOut])
async def list_skills(
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> list[SkillOut]:
    rows = (
        await db.execute(
            select(SkillLibraryEntry).order_by(
                SkillLibraryEntry.source, SkillLibraryEntry.name
            )
        )
    ).scalars().all()
    return [await _serialize(r, db) for r in rows]


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: str,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    entry = (
        await db.execute(
            select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    await db.delete(entry)
    await db.commit()
    return Response(status_code=204)


@router.post("/{skill_id}/attach", status_code=204)
async def attach_skill(
    skill_id: str,
    body: SkillAttachRequest,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    entry = (
        await db.execute(
            select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    agent = (
        await db.execute(
            select(Agent).where(Agent.id == body.agent_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    service = _service(request)
    await service.attach(db, agent_id=body.agent_id, skill_id=skill_id)
    await db.commit()
    return Response(status_code=204)


@router.delete("/{skill_id}/attach/{agent_id}", status_code=204)
async def detach_skill(
    skill_id: str,
    agent_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _service(request)
    await service.detach(db, agent_id=agent_id, skill_id=skill_id)
    await db.commit()
    return Response(status_code=204)
