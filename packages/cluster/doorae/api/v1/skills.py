"""REST endpoints for SkillLibrary — ``/api/v1/admin/skills``.

All endpoints require admin identity (``get_admin_identity`` dependency).
The actual SkillLibraryService instance lives on ``app.state`` so tests
can wire a fake fetcher without monkey-patching.

Mutations bump the generation of every agent that should see the
change so the machine daemon re-materializes the agent directory on
its next reconcile. Without these bumps the daemon's
``_reconcile_agent`` path treats ``current_gen >= manifest.generation``
as a no-op and skipping the materialize step leaves ``skills/`` empty
on disk even though the DB + manifest already reflect the attachment.

Phase 2 (#125) layering
-----------------------
Approval gate: attach refuses unapproved skills with 409 (defense in
depth — resolve_for_agent also filters, but blocking at attach time
surfaces the error to the admin UI instead of silently skipping the
skill on the next spawn). Approve + reject endpoints record audit
entries and bump attached agents so the first materialization lands
promptly after approval.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.db.models import (
    Agent,
    AgentSkill,
    SkillLibraryAudit,
    SkillLibraryEntry,
)
from doorae.dependencies import get_admin_identity, get_db
from doorae.skills_library.service import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    SkillLibraryService,
)

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
    # (#127) promoted this from "detected only" to "actually bundled
    # into the agent directory". Field name kept for frontend compat
    # (AdminSkills still renders ``scripts_detected.length`` as "+N
    # files" and the count matches the new semantics 1:1).
    scripts_detected: list[str]
    content_hash: str
    approved_by: Optional[str]
    approved_at: Optional[datetime]
    fetched_at: datetime
    # Derived status — "pending" / "approved" / "rejected". Computed
    # by the service using approved_by + last audit action.
    status: str
    # List of agent IDs currently attached. Kept out of
    # SkillLibraryEntry.model_config (``from_attributes``) because
    # it requires a join — the handler fills this in explicitly.
    attached_agent_ids: list[str] = []
    model_config = {"from_attributes": True}


class SkillPreviewOut(BaseModel):
    """Detailed view used by the preview dialog.

    Returns full SKILL.md body + an ``extra_files`` list so the admin
    can review file names (and count) before approving. Bodies are
    truncated to a cheap cap so the preview doesn't choke on a
    pathological skill; admins who want the real content can read the
    upstream repo.
    """
    id: str
    source: str
    name: str
    pinned_rev: str
    skill_md: str
    extra_files: list[str]
    content_hash: str
    status: str


class SkillAuditOut(BaseModel):
    id: str
    action: str
    actor_user_id: Optional[str]
    at: datetime
    detail: dict
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
    *,
    status: str,
) -> SkillOut:
    """Attach ``attached_agent_ids`` + derived status onto an ORM row."""
    agent_ids = await _attached_agent_ids(db, entry.id)
    return SkillOut(
        id=entry.id,
        source=entry.source,
        name=entry.name,
        pinned_rev=entry.pinned_rev,
        scripts_detected=list(entry.scripts_detected or []),
        content_hash=entry.content_hash,
        approved_by=entry.approved_by,
        approved_at=entry.approved_at,
        fetched_at=entry.fetched_at,
        status=status,
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
        actor_user_id=identity.id,
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
    status = await service.get_status(db, result.entry.id) or STATUS_PENDING
    return await _serialize(result.entry, db, status=status)


@router.get("", response_model=list[SkillOut])
async def list_skills(
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
    status: Optional[Literal["pending", "approved", "rejected"]] = Query(
        default=None,
        description="Filter by derived status.",
    ),
) -> list[SkillOut]:
    service = _service(request)
    pairs = await service.list_with_status(db, status=status)
    return [await _serialize(entry, db, status=st) for entry, st in pairs]


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _service(request)
    agent_ids, existed = await service.delete(
        skill_id=skill_id, actor_user_id=identity.id,
    )
    if not existed:
        raise HTTPException(status_code=404, detail="Skill not found")

    lifecycle = _lifecycle(request)
    for agent_id in agent_ids:
        await lifecycle.bump_generation(agent_id)
    return Response(status_code=204)


@router.post("/{skill_id}/approve", response_model=SkillOut)
async def approve_skill(
    skill_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> SkillOut:
    service = _service(request)
    result = await service.approve(skill_id=skill_id, actor_user_id=identity.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Any agent already attached to this skill will only see its
    # contents on disk after the next spawn, and the reconcile loop
    # treats ``current_gen >= desired`` as a no-op — so we must bump
    # the generation to force re-materialization on approval.
    lifecycle = _lifecycle(request)
    for agent_id in result.attached_agent_ids:
        await lifecycle.bump_generation(agent_id)

    status = await service.get_status(db, skill_id) or STATUS_APPROVED
    return await _serialize(result.entry, db, status=status)


@router.post("/{skill_id}/reject", response_model=SkillOut)
async def reject_skill(
    skill_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> SkillOut:
    service = _service(request)
    result = await service.reject(skill_id=skill_id, actor_user_id=identity.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Only previously-approved skills contribute to attached_agent_ids
    # here — the service returns [] for rejects of pending skills
    # because those never made it to disk in the first place.
    lifecycle = _lifecycle(request)
    for agent_id in result.attached_agent_ids:
        await lifecycle.bump_generation(agent_id)

    status = await service.get_status(db, skill_id) or STATUS_REJECTED
    return await _serialize(result.entry, db, status=status)


@router.get("/{skill_id}/preview", response_model=SkillPreviewOut)
async def preview_skill(
    skill_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> SkillPreviewOut:
    """Full SKILL.md body + file list for the approve dialog preview."""
    entry = (
        await db.execute(
            select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    service = _service(request)
    status = await service.get_status(db, skill_id) or STATUS_PENDING
    return SkillPreviewOut(
        id=entry.id,
        source=entry.source,
        name=entry.name,
        pinned_rev=entry.pinned_rev,
        skill_md=entry.skill_md,
        extra_files=sorted((entry.extra_files or {}).keys()),
        content_hash=entry.content_hash,
        status=status,
    )


@router.get("/{skill_id}/audits", response_model=list[SkillAuditOut])
async def list_audits(
    skill_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> list[SkillAuditOut]:
    # No 404 on missing skill — audits can outlive the referenced row
    # (FK SET NULL on delete), so the endpoint is lenient and returns
    # an empty list when nothing matches.
    entry = (
        await db.execute(
            select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    service = _service(request)
    rows: list[SkillLibraryAudit] = await service.list_audits(db, skill_id)
    return [
        SkillAuditOut(
            id=row.id,
            action=row.action,
            actor_user_id=row.actor_user_id,
            at=row.at,
            detail=dict(row.detail or {}),
        )
        for row in rows
    ]


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

    # Phase 2 gate: unapproved skills can't be attached. Defense in
    # depth — resolve_for_agent / _build_sync_frame also filter
    # unapproved rows out of the sync frame, but blocking here turns
    # the failure into a visible 409 instead of a silent spawn-time
    # skip.
    if entry.approved_by is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Skill is not approved; approve it before attaching."
            ),
        )

    agent = (
        await db.execute(
            select(Agent).where(Agent.id == body.agent_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    service = _service(request)
    did_insert = await service.attach(
        db,
        agent_id=body.agent_id,
        skill_id=skill_id,
        actor_user_id=identity.id,
    )
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
    did_delete = await service.detach(
        db,
        agent_id=agent_id,
        skill_id=skill_id,
        actor_user_id=identity.id,
    )
    await db.commit()
    if did_delete:
        lifecycle = _lifecycle(request)
        await lifecycle.bump_generation(agent_id)
    return Response(status_code=204)
