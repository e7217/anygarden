"""REST endpoints for metric-driven campaigns (#930 §3.8, Phase 7).

Surface (all under ``/api/v1``):

- ``POST  /rooms/{room_id}/campaigns``  — create; the room's lead agent runs it
- ``GET   /rooms/{room_id}/campaigns``  — the room's campaigns, newest first
- ``GET   /campaigns/{id}``             — one campaign
- ``PATCH /campaigns/{id}``             — edit purpose / criteria / evaluator
- ``POST  /campaigns/{id}/{pause|resume|stop|extend|confirm|reject|select-final}``
- ``GET   /campaigns/{id}/attempts``    — cycles and retrospectives, newest first
- ``GET   /campaigns/{id}/events``      — history, newest first
- ``POST  /campaigns/{id}/attempts/{attempt_id}/trusted-score``

Permissions: reading needs read access to the campaign's room; creating
needs task management in the room; every change needs the campaign's
owner or a global admin (who must still reach the room). Setting the
evaluator command — it runs on the agent's machine — additionally needs
the owner of that machine or an admin.

Every mutation commits and then broadcasts ``campaign.updated`` (and
``campaign.attempt_updated`` / ``task.updated`` for what it touched) to
the room (``campaigns.notify``). Errors use the v1 envelope
(``api/v1/errors.py``): ``code`` is one of the ``CAMPAIGN_*`` codes and
``reason`` carries the specific rule (e.g. ``TARGET_REQUIRED``).
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.api.v1.errors import make_api_error
from anygarden.auth.dependencies import Identity
from anygarden.campaigns import actions, notify
from anygarden.campaigns.policy import (
    MAX_EVAL_COMMAND_CHARS,
    MAX_EVAL_PATH_CHARS,
    MAX_EVAL_PIN_PATHS,
    MAX_EVAL_TIMEOUT_SECONDS,
    MAX_SCORE_KEY_CHARS,
    CampaignPolicyError,
)
from anygarden.campaigns.service import IllegalTransition
from anygarden.campaigns.views import attempt_view, campaign_view
from anygarden.db.models import (
    Agent,
    Campaign,
    CampaignAttempt,
    CampaignEvent,
    Machine,
    Room,
)
from anygarden.dependencies import get_current_identity, get_db
from anygarden.rooms.authorization import (
    Capability,
    is_global_admin,
    require_capability,
)

router = APIRouter(tags=["campaigns"])

IdentityDep = Annotated[Identity, Depends(get_current_identity)]
DbDep = Annotated[AsyncSession, Depends(get_db)]

MAX_PAGE = 100


# ── Schemas ────────────────────────────────────────────────────────


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _finite(value: float | None) -> float | None:
    if value is not None and not math.isfinite(value):
        raise ValueError("must be a finite number")
    return value


class MetricIn(_Strict):
    name: str = Field(min_length=1, max_length=120)
    direction: Literal["maximize", "minimize"]
    target: float | None = None
    min_delta: float = Field(default=0.0, ge=0)

    _finite_target = field_validator("target")(_finite)


class EvaluatorIn(_Strict):
    """Limits match ``CampaignEvalRequestFrame``: an evaluator the server
    stores must be one the machine can parse."""

    command: str = Field(min_length=1, max_length=MAX_EVAL_COMMAND_CHARS)
    cwd: str = Field(default=".", max_length=MAX_EVAL_PATH_CHARS)
    timeout_sec: int = Field(default=600, ge=1, le=MAX_EVAL_TIMEOUT_SECONDS)
    pin_paths: list[Annotated[str, Field(max_length=MAX_EVAL_PATH_CHARS)]] = Field(
        default_factory=list, max_length=MAX_EVAL_PIN_PATHS
    )
    score_key: str = Field(default="score", min_length=1, max_length=MAX_SCORE_KEY_CHARS)


class BudgetIn(_Strict):
    max_total_tokens: int
    retro_reserve_ratio: float = 0.05


class CampaignCreate(_Strict):
    title: str = Field(min_length=1, max_length=500)
    purpose: str = Field(min_length=1)
    mode: Literal["target", "optimize"]
    metric: MetricIn
    evaluator: EvaluatorIn
    budget: BudgetIn
    deadline_at: AwareDatetime | None = None
    trusted_eval: Literal["none", "external"] = "none"


class MetricPatch(_Strict):
    target: float | None = None
    min_delta: float | None = Field(default=None, ge=0)

    _finite_target = field_validator("target")(_finite)


class BudgetPatch(_Strict):
    max_total_tokens: int | None = None
    retro_reserve_ratio: float | None = None


class CampaignUpdate(_Strict):
    """Only the fields sent change. ``deadline_at: null`` clears the
    deadline (a target campaign only)."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    purpose: str | None = Field(default=None, min_length=1)
    metric: MetricPatch | None = None
    deadline_at: AwareDatetime | None = None
    budget: BudgetPatch | None = None
    evaluator: EvaluatorIn | None = None
    trusted_eval: Literal["none", "external"] | None = None


class ExtendIn(_Strict):
    deadline_at: AwareDatetime | None = None
    max_total_tokens: int | None = None


class RejectIn(_Strict):
    reason: str | None = Field(default=None, max_length=2000)


class SelectFinalIn(_Strict):
    attempt_id: str


class TrustedScoreIn(_Strict):
    score: float

    _finite_score = field_validator("score")(_finite)


class ScoreRef(BaseModel):
    attempt_id: str
    cycle_no: int
    dev_score: float | None
    trusted_score: float | None
    verified: bool
    """``True`` when the score is externally verified (trusted)."""


class OpenAttemptRef(BaseModel):
    attempt_id: str
    cycle_no: int
    kind: str
    eval_status: str
    task_id: str | None


class CampaignOut(BaseModel):
    id: str
    room_id: str
    owner_id: str | None
    agent_id: str | None
    title: str
    purpose: str
    mode: str
    metric: dict[str, Any]
    evaluator: dict[str, Any]
    trusted_eval: str
    budget: dict[str, Any]
    usable_tokens: int
    """Tokens cycles may spend (the retrospective reserve excluded)."""
    deadline_at: str | None
    status: str
    stop_reason: str | None
    status_detail: str | None
    cycles_run: int
    tokens_used: int
    best: ScoreRef | None
    final: ScoreRef | None
    open_attempt: OpenAttemptRef | None
    next_cycle_at: str | None
    cycles_since_improvement: int
    consecutive_eval_failures: int
    retro_due: str | None
    state_revision: int
    created_at: str | None
    updated_at: str | None


class CampaignAttemptOut(BaseModel):
    id: str
    campaign_id: str
    cycle_no: int
    kind: str
    task_id: str | None
    execution_id: str | None
    opened_at: str | None
    closed_at: str | None
    agent_outcome: str | None
    hypothesis: str | None
    change_summary: str | None
    artifact_ref: str | None
    lessons: str | None
    recorded_at: str | None
    wake_at: str | None
    wake_reason: str | None
    block_reason: str | None
    unblock_hint: str | None
    eval_status: str
    dev_score: float | None
    metrics: dict[str, Any] | None
    valid: bool | None
    eval_excerpt: str | None
    eval_started_at: str | None
    eval_finished_at: str | None
    trusted_score: float | None
    trusted_at: str | None
    tokens: int | None
    outcome: str | None


class CampaignEventOut(BaseModel):
    id: str
    type: str
    details: dict[str, Any] | None
    actor: str | None
    created_at: str | None


class AttemptPage(BaseModel):
    items: list[CampaignAttemptOut]
    total: int
    next_offset: int | None


class EventPage(BaseModel):
    items: list[CampaignEventOut]
    total: int
    next_offset: int | None


# ── Errors ─────────────────────────────────────────────────────────


def _not_found():
    return make_api_error(code="CAMPAIGN_NOT_FOUND", status_code=404, message="Campaign not found")


def _attempt_not_found():
    return make_api_error(
        code="CAMPAIGN_ATTEMPT_NOT_FOUND", status_code=404, message="Campaign attempt not found"
    )


def _forbidden():
    return make_api_error(
        code="CAMPAIGN_FORBIDDEN", status_code=403,
        message="Only the campaign's owner or an admin can change it",
    )


def _evaluator_forbidden():
    return make_api_error(
        code="CAMPAIGN_EVALUATOR_FORBIDDEN", status_code=403,
        message=(
            "The evaluator command runs on the agent's machine: only that machine's "
            "owner or an admin can set it"
        ),
    )


def _invalid(exc: CampaignPolicyError):
    return make_api_error(
        code="CAMPAIGN_INVALID", status_code=422, message=exc.message, reason=exc.code
    )


def _policy_error(exc: CampaignPolicyError):
    """A broken campaign rule: an archived room is a conflict (409), any
    other rule an invalid request (422)."""
    if exc.code == "ROOM_ARCHIVED":
        return _conflict(exc.code, exc.message)
    return _invalid(exc)


def _conflict(reason: str, message: str):
    return make_api_error(
        code="CAMPAIGN_CONFLICT", status_code=409, message=message, reason=reason
    )


def _user_required():
    return make_api_error(
        code="CAMPAIGN_FORBIDDEN", status_code=403, message="A user account is required"
    )


# ── Helpers ────────────────────────────────────────────────────────


def _actor(identity: Identity) -> str:
    return f"user:{identity.id}"


async def _load(db: AsyncSession, campaign_id: str, identity: Identity) -> Campaign:
    """The campaign, if *identity* may read its room."""
    campaign = await db.get(Campaign, campaign_id, populate_existing=True)
    if campaign is None:
        raise _not_found()
    await require_capability(
        db, room_id=campaign.room_id, identity=identity, capability=Capability.TASK_READ
    )
    return campaign


async def _load_owned(db: AsyncSession, campaign_id: str, identity: Identity) -> Campaign:
    if identity.kind != "user":
        raise _user_required()
    campaign = await _load(db, campaign_id, identity)
    if campaign.owner_id != identity.id and not is_global_admin(identity):
        raise _forbidden()
    return campaign


async def _can_set_evaluator(db: AsyncSession, identity: Identity, agent_id: str | None) -> bool:
    """The owner of the agent's machine, or an admin. An agent not placed
    on a machine yet has no owner to check: only an admin may then."""
    if is_global_admin(identity):
        return True
    agent = await db.get(Agent, agent_id) if agent_id else None
    if agent is None or agent.placed_on_machine_id is None:
        return False
    machine = await db.get(Machine, agent.placed_on_machine_id)
    return machine is not None and machine.owner_user_id == identity.id


async def _respond(request: Request, db: AsyncSession, campaign: Campaign) -> dict:
    notify.mark(db, campaign_id=campaign.id)
    await notify.commit_and_publish(db, getattr(request.app.state, "connection_manager", None))
    await db.refresh(campaign)
    return await campaign_view(db, campaign)


async def _run(request: Request, db: AsyncSession, campaign: Campaign, action) -> dict:
    """Run *action* and map the campaign rules onto the v1 envelope."""
    try:
        await action()
    except CampaignPolicyError as exc:
        raise _policy_error(exc) from None
    except IllegalTransition as exc:
        raise _conflict(
            "ILLEGAL_TRANSITION",
            f"Not possible while the campaign is {exc.status}",
        ) from None
    except actions.CampaignConflict as exc:
        raise _conflict(exc.code, exc.message) from None
    except actions.CampaignAttemptNotFound:
        raise _attempt_not_found() from None
    return await _respond(request, db, campaign)


# ── Routes ─────────────────────────────────────────────────────────


@router.post("/api/v1/rooms/{room_id}/campaigns", status_code=201, response_model=CampaignOut)
async def create_campaign(
    *,
    room_id: str,
    body: CampaignCreate,
    request: Request,
    identity: IdentityDep,
    db: DbDep,
):
    """Start a campaign in a project room; the room's lead agent runs it."""
    from anygarden.campaigns.service import check_campaign_room
    from anygarden.campaigns.service import create_campaign as create

    if identity.kind != "user":
        raise _user_required()
    access = await require_capability(
        db, room_id=room_id, identity=identity, capability=Capability.TASK_MANAGE
    )
    room: Room = access.room
    try:
        # The room's own rules first: without a lead there is no machine
        # whose owner could set the evaluator.
        await check_campaign_room(db, room)
    except CampaignPolicyError as exc:
        raise _policy_error(exc) from None
    if not await _can_set_evaluator(db, identity, room.representative_agent_id):
        raise _evaluator_forbidden()
    try:
        campaign = await create(
            db, owner_id=identity.id, room=room, title=body.title, purpose=body.purpose,
            mode=body.mode, metric=body.metric.model_dump(),
            evaluator=body.evaluator.model_dump(), budget=body.budget.model_dump(),
            deadline_at=body.deadline_at, trusted_eval=body.trusted_eval,
        )
    except CampaignPolicyError as exc:
        raise _policy_error(exc) from None
    return await _respond(request, db, campaign)


@router.get("/api/v1/rooms/{room_id}/campaigns", response_model=list[CampaignOut])
async def list_room_campaigns(
    *,
    room_id: str,
    status: Annotated[str | None, Query(max_length=32)] = None,
    identity: IdentityDep,
    db: DbDep,
):
    await require_capability(db, room_id=room_id, identity=identity, capability=Capability.TASK_READ)
    stmt = select(Campaign).where(Campaign.room_id == room_id)
    if status:
        stmt = stmt.where(Campaign.status == status)
    rows = list(await db.scalars(stmt.order_by(Campaign.created_at.desc())))
    return [await campaign_view(db, row) for row in rows]


@router.get("/api/v1/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    *,
    campaign_id: str,
    identity: IdentityDep,
    db: DbDep,
):
    return await campaign_view(db, await _load(db, campaign_id, identity))


@router.patch("/api/v1/campaigns/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    *,
    campaign_id: str,
    body: CampaignUpdate,
    request: Request,
    identity: IdentityDep,
    db: DbDep,
):
    """Edit; applies from the next cycle, and an open cycle's agent gets it
    as a follow-up directive."""
    campaign = await _load_owned(db, campaign_id, identity)
    changes: dict[str, Any] = {}
    for field in ("title", "purpose", "trusted_eval"):
        if getattr(body, field) is not None:
            changes[field] = getattr(body, field)
    if "deadline_at" in body.model_fields_set:
        changes["deadline_at"] = body.deadline_at
    if body.metric is not None:
        changes["metric"] = body.metric.model_dump(exclude_unset=True)
    if body.budget is not None:
        changes["budget"] = body.budget.model_dump(exclude_none=True)
    if body.evaluator is not None:
        if not await _can_set_evaluator(db, identity, campaign.agent_id):
            raise _evaluator_forbidden()
        changes["evaluator"] = body.evaluator.model_dump()

    async def action():
        await actions.edit_campaign(db, campaign, actor=_actor(identity), changes=changes)

    return await _run(request, db, campaign, action)


@router.post("/api/v1/campaigns/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(
    *,
    campaign_id: str, request: Request,
    identity: IdentityDep, db: DbDep,
):
    campaign = await _load_owned(db, campaign_id, identity)
    return await _run(request, db, campaign, lambda: actions.pause_campaign(
        db, campaign, actor=_actor(identity)))


@router.post("/api/v1/campaigns/{campaign_id}/resume", response_model=CampaignOut)
async def resume_campaign(
    *,
    campaign_id: str, request: Request,
    identity: IdentityDep, db: DbDep,
):
    campaign = await _load_owned(db, campaign_id, identity)
    return await _run(request, db, campaign, lambda: actions.resume_campaign(
        db, campaign, actor=_actor(identity)))


@router.post("/api/v1/campaigns/{campaign_id}/stop", response_model=CampaignOut)
async def stop_campaign(
    *,
    campaign_id: str, request: Request,
    identity: IdentityDep, db: DbDep,
):
    """End the campaign for good; its open cycle closes (no retrospective)."""
    from anygarden.campaigns.lifecycle import stop_campaign as stop

    campaign = await _load_owned(db, campaign_id, identity)

    async def action():
        if not await stop(db, campaign, actor=_actor(identity), stop_reason="user"):
            raise actions.CampaignConflict("CAMPAIGN_ENDED", "The campaign has already ended")

    return await _run(request, db, campaign, action)


@router.post("/api/v1/campaigns/{campaign_id}/extend", response_model=CampaignOut)
async def extend_campaign(
    *,
    campaign_id: str, body: ExtendIn, request: Request,
    identity: IdentityDep, db: DbDep,
):
    """Raise the deadline and/or token budget; reopens a campaign that
    ended on its deadline or budget when the extension clears both."""
    from anygarden.campaigns.actions import open_cycle_no
    from anygarden.campaigns.service import extend_campaign as extend

    campaign = await _load_owned(db, campaign_id, identity)

    async def action():
        cycle_no = await open_cycle_no(db, campaign)
        if not await extend(
            db, campaign, actor=_actor(identity), deadline_at=body.deadline_at,
            max_total_tokens=body.max_total_tokens,
        ):
            raise actions.CampaignConflict("CAMPAIGN_CHANGED", "The campaign changed meanwhile")
        if cycle_no is not None:
            # Stamp the extension for the open cycle's follow-up directives.
            event = await db.scalar(
                select(CampaignEvent).where(
                    CampaignEvent.campaign_id == campaign.id,
                    CampaignEvent.event_key == f"campaign:{campaign.id}:rev:{campaign.state_revision}",
                )
            )
            if event is not None:
                event.details = {**(event.details or {}), "open_cycle_no": cycle_no}

    return await _run(request, db, campaign, action)


@router.post("/api/v1/campaigns/{campaign_id}/confirm", response_model=CampaignOut)
async def confirm_campaign(
    *,
    campaign_id: str, request: Request,
    identity: IdentityDep, db: DbDep,
):
    """Accept the dev score that reached the target → ``achieved``."""
    campaign = await _load_owned(db, campaign_id, identity)
    return await _run(request, db, campaign, lambda: actions.confirm_campaign(
        db, campaign, actor=_actor(identity)))


@router.post("/api/v1/campaigns/{campaign_id}/reject", response_model=CampaignOut)
async def reject_campaign(
    *,
    campaign_id: str, request: Request, body: RejectIn | None = None,
    identity: IdentityDep, db: DbDep,
):
    """Reject the result awaiting confirmation; the loop goes on."""
    campaign = await _load_owned(db, campaign_id, identity)
    return await _run(request, db, campaign, lambda: actions.reject_campaign(
        db, campaign, actor=_actor(identity), reason=body.reason if body else None))


@router.post("/api/v1/campaigns/{campaign_id}/select-final", response_model=CampaignOut)
async def select_final(
    *,
    campaign_id: str, body: SelectFinalIn, request: Request,
    identity: IdentityDep, db: DbDep,
):
    campaign = await _load_owned(db, campaign_id, identity)
    return await _run(request, db, campaign, lambda: actions.select_final(
        db, campaign, actor=_actor(identity), attempt_id=body.attempt_id))


@router.post(
    "/api/v1/campaigns/{campaign_id}/attempts/{attempt_id}/trusted-score",
    response_model=CampaignOut,
)
async def record_trusted_score(
    *,
    campaign_id: str, attempt_id: str, body: TrustedScoreIn, request: Request,
    identity: IdentityDep, db: DbDep,
):
    """Record an externally verified score; in target mode reaching the
    target achieves the campaign."""
    campaign = await _load_owned(db, campaign_id, identity)
    return await _run(request, db, campaign, lambda: actions.record_trusted_score(
        db, campaign, actor=_actor(identity), attempt_id=attempt_id, score=body.score))


@router.get("/api/v1/campaigns/{campaign_id}/attempts", response_model=AttemptPage)
async def list_attempts(
    *,
    campaign_id: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 20,
    identity: IdentityDep,
    db: DbDep,
):
    campaign = await _load(db, campaign_id, identity)
    total = int(await db.scalar(
        select(func.count()).select_from(CampaignAttempt)
        .where(CampaignAttempt.campaign_id == campaign.id)
    ) or 0)
    rows = list(await db.scalars(
        select(CampaignAttempt).where(CampaignAttempt.campaign_id == campaign.id)
        .order_by(CampaignAttempt.cycle_no.desc()).offset(offset).limit(limit)
    ))
    end = offset + len(rows)
    return {"items": [attempt_view(r) for r in rows], "total": total,
            "next_offset": end if end < total else None}


@router.get("/api/v1/campaigns/{campaign_id}/events", response_model=EventPage)
async def list_events(
    *,
    campaign_id: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    identity: IdentityDep,
    db: DbDep,
):
    campaign = await _load(db, campaign_id, identity)
    total = int(await db.scalar(
        select(func.count()).select_from(CampaignEvent)
        .where(CampaignEvent.campaign_id == campaign.id)
    ) or 0)
    rows = list(await db.scalars(
        select(CampaignEvent).where(CampaignEvent.campaign_id == campaign.id)
        .order_by(CampaignEvent.created_at.desc(), CampaignEvent.id.desc())
        .offset(offset).limit(limit)
    ))
    end = offset + len(rows)
    return {
        "items": [
            {"id": r.id, "type": r.type, "details": r.details, "actor": r.actor,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ],
        "total": total,
        "next_offset": end if end < total else None,
    }


__all__ = ["router"]
