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

import time
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
from doorae.skills_library.search import (
    SkillSearchError,
    search_skills as skills_sh_search,
)
from doorae.skills_library.service import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    SkillLibraryService,
    StaleCheckResult,
)


# #126 — search response cache TTL. 60s is long enough to absorb an
# admin's rapid-fire typing (incremental filter UX) but short enough
# that a fresh "popular skills" list is never more than a minute
# stale. Decision C in plan §3.2.
_SEARCH_CACHE_TTL_SECONDS = 60.0

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
    # #120 — agent-authored rows carry the author's agent id here;
    # shared / admin-registered rows keep this NULL.  Exposed so the
    # admin UI can filter and render a "Promote" button on
    # agent-authored rows.
    created_by_agent_id: Optional[str] = None
    fetched_at: datetime
    # Derived status — "pending" / "approved" / "rejected". Computed
    # by the service using approved_by + last audit action.
    status: str
    # List of agent IDs currently attached. Kept out of
    # SkillLibraryEntry.model_config (``from_attributes``) because
    # it requires a join — the handler fills this in explicitly.
    attached_agent_ids: list[str] = []
    # #126 — merged from the in-memory stale cache maintained by the
    # cron task. ``True`` when upstream HEAD has moved past the
    # pinned_rev on disk; ``False`` when we've checked and they
    # match, OR when we haven't probed this skill yet (conservative
    # default — the UI only shows "Update available" badges for
    # explicit ``True`` values).
    stale: bool = False
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


class SkillSearchResultOut(BaseModel):
    """One hit from ``GET /admin/skills/search`` — #126.

    Shape mirrors skills.sh ``/api/search`` rows so the admin UI can
    render the table without a second lookup. The ``source`` field is
    what the admin feeds back into ``POST /admin/skills`` to register.
    """
    id: str
    skillId: str  # noqa: N815 — upstream naming
    name: str
    installs: int
    source: str


class SkillStaleOut(BaseModel):
    """One entry from the stale-check cache. Used by
    ``GET /admin/skills/stale`` (#126)."""
    skill_id: str
    pinned_rev: str
    current_sha: Optional[str]
    stale: bool
    error: Optional[str] = None


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


def _stale_cache(request: Request) -> dict[str, StaleCheckResult]:
    """Return the mutable in-memory stale cache.

    Decision A1 in plan §3.2 — server-local dict, re-populated by the
    cron on restart. Lazy-initialised so tests that don't exercise the
    cron still work (the cache just stays empty → ``stale`` defaults
    to ``False`` everywhere).
    """
    cache = getattr(request.app.state, "skill_stale_cache", None)
    if cache is None:
        cache = {}
        request.app.state.skill_stale_cache = cache
    return cache


def _search_cache(
    request: Request,
) -> dict[tuple[str, int], tuple[float, list]]:
    """Return the (query, limit) → (timestamp, results) cache.

    Values are stored as lists of ``SearchResult`` dataclasses so the
    handler can re-wrap them into Pydantic shapes on each hit.
    """
    cache = getattr(request.app.state, "skill_search_cache", None)
    if cache is None:
        cache = {}
        request.app.state.skill_search_cache = cache
    return cache


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
    stale_cache: Optional[dict[str, StaleCheckResult]] = None,
) -> SkillOut:
    """Attach ``attached_agent_ids`` + derived status onto an ORM row.

    ``stale_cache`` is the app-level in-memory cache; the flag flips to
    ``True`` only when the cron has observed drift for this specific
    skill id. Omitted / missing entries collapse to ``False`` so the
    list endpoint is safe to call before the cron has ever ticked.
    """
    agent_ids = await _attached_agent_ids(db, entry.id)
    stale_flag = False
    if stale_cache is not None:
        cached = stale_cache.get(entry.id)
        if cached is not None:
            stale_flag = bool(cached.stale)
    return SkillOut(
        id=entry.id,
        source=entry.source,
        name=entry.name,
        pinned_rev=entry.pinned_rev,
        scripts_detected=list(entry.scripts_detected or []),
        content_hash=entry.content_hash,
        approved_by=entry.approved_by,
        approved_at=entry.approved_at,
        created_by_agent_id=entry.created_by_agent_id,
        fetched_at=entry.fetched_at,
        status=status,
        attached_agent_ids=list(agent_ids),
        stale=stale_flag,
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
    return await _serialize(
        result.entry, db, status=status, stale_cache=_stale_cache(request),
    )


@router.get("", response_model=list[SkillOut])
async def list_skills(
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
    status: Optional[Literal["pending", "approved", "rejected"]] = Query(
        default=None,
        description="Filter by derived status.",
    ),
    filter: Optional[Literal["agent_authored"]] = Query(
        default=None,
        description=(
            "Additional filter orthogonal to ``status``. "
            "``agent_authored`` keeps only rows with a non-NULL "
            "``created_by_agent_id`` so the admin UI can surface the "
            "Agent-authored tab (#120)."
        ),
    ),
) -> list[SkillOut]:
    """Admin listing.

    ``status=pending|approved|rejected`` filters by the #125 approval
    gate. ``filter=agent_authored`` is orthogonal and narrows to rows
    authored by agents via the MCP channel (#120). The two compose so
    an admin can, e.g., see "agent-authored rows waiting for promote".
    """
    service = _service(request)
    pairs = await service.list_with_status(db, status=status)
    if filter == "agent_authored":
        pairs = [(entry, st) for entry, st in pairs if entry.created_by_agent_id is not None]
    stale_cache = _stale_cache(request)
    return [
        await _serialize(entry, db, status=st, stale_cache=stale_cache)
        for entry, st in pairs
    ]


@router.post("/{skill_id}/promote", response_model=SkillOut)
async def promote_skill(
    skill_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> SkillOut:
    """Promote an agent-authored skill into the shared library (#120).

    Clears ``created_by_agent_id`` and stamps ``approved_by`` with the
    admin's user id so ``resolve_for_agent`` will surface the row for
    any agent that attaches to it afterwards.  The existing
    ``attach`` endpoint remains the way to fan the skill out to other
    agents.
    """
    service = _service(request)
    try:
        entry = await service.promote_to_shared(
            skill_id=skill_id, admin_user_id=identity.id
        )
    except Exception as exc:
        # The service raises SkillNotFoundError via a string-typed
        # RuntimeError subclass; map any failure onto a 404/500 the
        # UI can display.
        from doorae.skills_library.service import SkillNotFoundError
        if isinstance(exc, SkillNotFoundError):
            raise HTTPException(status_code=404, detail=str(exc))
        raise

    # Bump every already-attached agent so the promotion (which only
    # changes metadata, not body) still triggers a re-materialize —
    # the row now carries ``approved_by`` which resolves it through
    # the #125 gate for any agent, not just the original author.
    agent_ids = await _attached_agent_ids(db, entry.id)
    lifecycle = _lifecycle(request)
    for agent_id in agent_ids:
        await lifecycle.bump_generation(agent_id)

    status = await service.get_status(db, entry.id) or STATUS_PENDING
    return await _serialize(
        entry, db, status=status, stale_cache=_stale_cache(request),
    )


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
    return await _serialize(
        result.entry, db, status=status, stale_cache=_stale_cache(request),
    )


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
    return await _serialize(
        result.entry, db, status=status, stale_cache=_stale_cache(request),
    )


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


# ── #126 — search proxy + stale check + refresh ──────────────────────


@router.get("/search", response_model=list[SkillSearchResultOut])
async def search_skills_endpoint(
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    q: str = Query(default="", description="Search query passed to skills.sh."),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[SkillSearchResultOut]:
    """Proxy to skills.sh ``/api/search``.

    A 60s TTL memo cache fronts the call so an admin's incremental-
    search typing doesn't hammer skills.sh (plan §3.2 C). Upstream
    failures return 502 with a short detail so the UI can render a
    "search unavailable" fallback instead of a generic 5xx.
    """
    cache = _search_cache(request)
    key = (q, limit)
    now = time.monotonic()
    cached = cache.get(key)
    if cached is not None:
        ts, results = cached
        if now - ts < _SEARCH_CACHE_TTL_SECONDS:
            return [SkillSearchResultOut(**r.to_dict()) for r in results]

    try:
        results = await skills_sh_search(q, limit=limit)
    except SkillSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    cache[key] = (now, results)
    # Light cache eviction — keep dict size bounded. 100 entries is
    # overkill for a single admin's session; beyond that we drop the
    # oldest regardless of TTL to avoid unbounded growth.
    if len(cache) > 100:
        oldest = min(cache.items(), key=lambda kv: kv[1][0])[0]
        cache.pop(oldest, None)

    return [SkillSearchResultOut(**r.to_dict()) for r in results]


@router.get("/stale", response_model=list[SkillStaleOut])
async def list_stale_skills(
    request: Request,
    identity: Identity = Depends(get_admin_identity),
) -> list[SkillStaleOut]:
    """Return the current in-memory stale-check cache (#126).

    Cache is populated by the background cron (``app.py`` lifespan).
    Empty on fresh boot until the first sweep finishes — that's fine,
    the list view's ``stale=False`` default is the right conservative
    value for "not yet probed".
    """
    cache = _stale_cache(request)
    return [
        SkillStaleOut(
            skill_id=r.skill_id,
            pinned_rev=r.pinned_rev,
            current_sha=r.current_sha,
            stale=r.stale,
            error=r.error,
        )
        for r in cache.values()
    ]


@router.post("/{skill_id}/refresh", response_model=SkillOut)
async def refresh_skill(
    skill_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> SkillOut:
    """Re-register a skill against upstream HEAD (#126).

    Fetches the latest tree + bodies and re-calls ``service.register``
    for the same ``(source, name)``. Per Phase 2 (#125) / Phase 1
    semantics, a new ``pinned_rev`` creates a sibling row that starts
    at ``approved_by=NULL`` — the admin must approve the refreshed row
    before it can be attached. Same-SHA refresh is a safe idempotent
    no-op on the existing row (only recomputes ``content_hash``).

    Drops the stale-cache entry for the source row so the UI badge
    flips off immediately; the next cron tick will re-probe and
    re-populate if another drift has happened.
    """
    entry = (
        await db.execute(
            select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Agent-authored rows have no upstream to re-fetch from — the UI
    # hides the button for these, but guard the endpoint against a
    # direct curl so a 400 surfaces instead of an opaque GitHub 404.
    if entry.source.startswith("agent-authored:"):
        raise HTTPException(
            status_code=400,
            detail="Cannot refresh agent-authored skill (no upstream).",
        )

    service = _service(request)
    result = await service.register(
        source=entry.source,
        name=entry.name,
        rev="HEAD",
        actor_user_id=identity.id,
    )
    if result.body_changed:
        # If register() returned the same row (same SHA, idempotent
        # update with changed body), bump every attached agent so the
        # new content lands on disk. A sibling row created for a new
        # SHA has no agents attached yet — the bump loop is a no-op
        # in that case.
        agent_ids = await _attached_agent_ids(db, result.entry.id)
        lifecycle = _lifecycle(request)
        for agent_id in agent_ids:
            await lifecycle.bump_generation(agent_id)

    # Clear the stale marker for the skill we just refreshed. If the
    # refresh produced a sibling row (new pinned_rev), the OLD row's
    # stale flag is also wrong — drop it too so the UI doesn't keep
    # showing "Update available" on a row the admin already acted on.
    stale = _stale_cache(request)
    stale.pop(skill_id, None)
    stale.pop(result.entry.id, None)

    status = (
        await service.get_status(db, result.entry.id) or STATUS_PENDING
    )
    return await _serialize(
        result.entry, db, status=status, stale_cache=stale,
    )
