"""REST endpoints for SkillLibrary — ``/api/v1/admin/skills`` (#119 Phase 1).

All endpoints require admin identity (``get_admin_identity`` dependency).
The actual SkillLibraryService instance lives on ``app.state`` so tests
can wire a fake fetcher without monkey-patching.

Mutations bump the generation of every agent that should see the
change so the machine daemon re-materializes the agent directory on
its next reconcile. Without these bumps the daemon's
``_reconcile_agent`` path treats ``current_gen >= manifest.generation``
as a no-op and skipping the materialize step leaves ``skills/`` empty
on disk even though the DB + manifest already reflect the attachment.
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
    # Paths of every extra file fetched alongside SKILL.md — Phase 3
    # (#123) promoted this from "detected only" to "actually bundled
    # into the agent directory". Field name kept for frontend compat
    # (AdminSkills still renders ``scripts_detected.length`` as "+N
    # files" and the count matches the new semantics 1:1).
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


def _lifecycle(request: Request):
    # Typed as Any here to avoid circular imports — AgentLifecycle is
    # set by the app factory in lifespan. In test setups app.state is
    # populated before the client issues any request.
    return request.app.state.agent_lifecycle


async def _attached_agent_ids(db: AsyncSession, skill_id: str) -> list[str]:
    return list(
        (
            await db.execute(
                select(AgentSkill.agent_id).where(
                    AgentSkill.skill_library_id == skill_id
                )
            )
        ).scalars().all()
    )


async def _serialize(
    entry: SkillLibraryEntry,
    db: AsyncSession,
) -> SkillOut:
    """Attach ``attached_agent_ids`` onto an ORM row."""
    agent_ids = await _attached_agent_ids(db, entry.id)
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
    result = await service.register(
        source=body.source,
        name=body.name,
        rev=body.rev,
    )
    # When the upsert actually changed the stored body, every agent
    # already attached to this skill needs to pick up the new content
    # on its next spawn. A pure no-op re-register (same hash) skips
    # the bump so admins can safely re-hit the endpoint to force a
    # network refresh without kicking every dependent agent.
    if result.body_changed:
        agent_ids = await _attached_agent_ids(db, result.entry.id)
        lifecycle = _lifecycle(request)
        for agent_id in agent_ids:
            await lifecycle.bump_generation(agent_id)
    return await _serialize(result.entry, db)


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

    # Snapshot attached agent IDs BEFORE the delete — the CASCADE on
    # agent_skills wipes those rows atomically with the skill row, and
    # the post-delete query would otherwise come back empty.
    affected_agent_ids = await _attached_agent_ids(db, skill_id)

    await db.delete(entry)
    await db.commit()

    lifecycle = _lifecycle(request)
    for agent_id in affected_agent_ids:
        await lifecycle.bump_generation(agent_id)
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
    did_insert = await service.attach(db, agent_id=body.agent_id, skill_id=skill_id)
    await db.commit()
    if did_insert:
        lifecycle = _lifecycle(request)
        await lifecycle.bump_generation(body.agent_id)
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
    did_delete = await service.detach(db, agent_id=agent_id, skill_id=skill_id)
    await db.commit()
    if did_delete:
        lifecycle = _lifecycle(request)
        await lifecycle.bump_generation(agent_id)
    return Response(status_code=204)
