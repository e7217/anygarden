"""REST endpoints for autonomous responsibility (Goal) management
(#302 Phase 2).

Surface (all under ``/api/v1``):
- ``POST   /agents/{agent_id}/goals``         — register a goal
- ``GET    /agents/{agent_id}/goals``         — list this agent's goals
- ``GET    /rooms/{room_id}/goals``           — list goals reporting here
- ``GET    /goals/{goal_id}``                 — single goal
- ``PATCH  /goals/{goal_id}``                 — edit (title/spec/cron/...)
- ``DELETE /goals/{goal_id}``                 — remove goal
- ``POST   /goals/{goal_id}/run``             — fire one execution now
- ``POST   /goals/{goal_id}/scheduled-events`` — deliver one exact due slot
- ``POST   /goals/{goal_id}/pause``           — flip status='paused'
- ``POST   /goals/{goal_id}/resume``          — flip status='active' +
                                                 recompute next_run_at

Permissions: the caller must be the goal's owner OR a global admin.
The agent's room membership is validated at create time so the
scheduler isn't constantly catching ``GoalExecutionError``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.auth.jwt import UserClaims
from anygarden.db.models import Agent, Goal, Participant, Room
from anygarden.dependencies import get_current_identity, get_db
from anygarden.goals.executor import GoalExecutionError, trigger_goal
from anygarden.goals.policy import (
    InvalidTriggerConfig,
    compute_next_run_at,
    validate_trigger_config,
)
from anygarden.goals.scheduler import GoalScheduler, ScheduledEventError
from anygarden.rooms.authorization import require_active_room

router = APIRouter(tags=["goals"])

# #449 (Wave 1b) — per-owner active-goal cap. A misconfigured client
# (or an abusive one) could otherwise register unbounded goals, each
# of which the scheduler fires on its own cadence — a slow-burn cost
# runaway. 50 active goals per owner is far above any legitimate
# single-user need while bounding the blast radius. Paused / terminal
# goals don't count (they don't fire).
MAX_ACTIVE_GOALS_PER_OWNER: int = 50


# ── Schemas ────────────────────────────────────────────────────────


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    spec: str = Field(min_length=1)
    trigger_type: str = Field(pattern=r"^(cron|interval|manual)$")
    trigger_config: dict
    materialize: str = Field(
        default="interesting_only",
        pattern=r"^(full|interesting_only)$",
    )
    report_room_id: Optional[str] = None


class GoalUpdate(BaseModel):
    title: Optional[str] = None
    spec: Optional[str] = None
    trigger_type: Optional[str] = Field(
        default=None, pattern=r"^(cron|interval|manual)$"
    )
    trigger_config: Optional[dict] = None
    materialize: Optional[str] = Field(
        default=None, pattern=r"^(full|interesting_only)$"
    )
    report_room_id: Optional[str] = None
    status: Optional[str] = Field(
        default=None, pattern=r"^(active|paused|completed|abandoned)$"
    )


class GoalOut(BaseModel):
    id: str
    assignee_agent_id: str
    owner_id: str
    report_room_id: Optional[str]
    title: str
    spec: str
    status: str
    trigger_type: str
    trigger_config: dict
    materialize: str
    consecutive_failures: int
    next_run_at: Optional[str]
    last_run_at: Optional[str]
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class ScheduledEventIn(BaseModel):
    scheduled_for: AwareDatetime

    model_config = {"extra": "forbid"}

    @field_validator("scheduled_for", mode="before")
    @classmethod
    def require_iso_timestamp(cls, value):
        if not isinstance(value, str):
            # Pydantic maps ValueError to 422; TypeError escapes its validation.
            raise ValueError("scheduled_for must be a timezone-aware ISO timestamp")  # noqa: TRY004
        return datetime.fromisoformat(value)


class ScheduledEventOut(BaseModel):
    goal_id: str
    scheduled_for: str
    status: Literal["accepted", "duplicate", "waiting_overlap"]
    task_id: str | None
    duplicate: bool
    schedule_context: dict | None


# ── Helpers ────────────────────────────────────────────────────────


def _to_out(goal: Goal) -> GoalOut:
    def _iso(dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt is not None else None

    return GoalOut(
        id=goal.id,
        assignee_agent_id=goal.assignee_agent_id,
        owner_id=goal.owner_id,
        report_room_id=goal.report_room_id,
        title=goal.title,
        spec=goal.spec,
        status=goal.status,
        trigger_type=goal.trigger_type,
        trigger_config=goal.trigger_config,
        materialize=goal.materialize,
        consecutive_failures=goal.consecutive_failures,
        next_run_at=_iso(goal.next_run_at),
        last_run_at=_iso(goal.last_run_at),
        created_at=_iso(goal.created_at) or "",
        updated_at=_iso(goal.updated_at) or "",
    )


async def _ensure_agent_in_room(
    db: AsyncSession, agent_id: str, room_id: Optional[str]
) -> None:
    """Validate the agent is a participant of *room_id*. ``None`` room
    is allowed (silent goal). Raises 422 with an actionable message
    when the membership is missing — the scheduler would otherwise
    hit ``GoalExecutionError`` on the very first fire."""
    if room_id is None:
        return
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="report_room_id not found")
    require_active_room(room)
    p = (
        await db.execute(
            select(Participant).where(
                Participant.room_id == room_id,
                Participant.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"agent {agent_id} is not a participant of room "
                f"{room_id}; add it before creating the goal"
            ),
        )


async def _load_goal_owned(
    db: AsyncSession, goal_id: str, identity: Identity
) -> Goal:
    goal = await db.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    if identity.kind == "user":
        claims = identity.claims
        is_admin = (isinstance(claims, UserClaims)
                    and claims.user_id == identity.id and claims.is_admin is True)
        if goal.owner_id != identity.id and not is_admin:
            raise HTTPException(status_code=403, detail="forbidden")
    return goal


async def _load_scheduled_goal_owned(
    db: AsyncSession, goal_id: str, identity: Identity
) -> Goal:
    """Require registered owner/admin claims for exact scheduled delivery."""
    claims = identity.claims
    if (identity.kind != "user" or not isinstance(claims, UserClaims)
        or claims.user_id != identity.id):
        raise HTTPException(status_code=403, detail="Goal owner or admin required")
    goal = await db.get(Goal, goal_id, populate_existing=True)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    if goal.owner_id != identity.id and claims.is_admin is not True:
        raise HTTPException(status_code=403, detail="forbidden")
    return goal


# ── Routes ─────────────────────────────────────────────────────────


@router.post(
    "/api/v1/agents/{agent_id}/goals",
    status_code=201,
    response_model=GoalOut,
)
async def create_goal(
    agent_id: str,
    body: GoalCreate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Register a new responsibility for *agent_id*."""
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="user identity required")
    try:
        validate_trigger_config(body.trigger_type, body.trigger_config)
    except InvalidTriggerConfig as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # #449 — per-owner active-goal cap. Reject before doing any other
    # work so an abusive caller can't accumulate fire-on-cadence goals.
    active_count = (
        await db.execute(
            select(func.count())
            .select_from(Goal)
            .where(Goal.owner_id == identity.id, Goal.status == "active")
        )
    ).scalar_one()
    if active_count >= MAX_ACTIVE_GOALS_PER_OWNER:
        raise HTTPException(
            status_code=422,
            detail=(
                f"active goal limit reached "
                f"({MAX_ACTIVE_GOALS_PER_OWNER} per owner); pause or "
                f"delete an existing goal before creating another"
            ),
        )

    await _ensure_agent_in_room(db, agent_id, body.report_room_id)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    next_run_at = compute_next_run_at(
        body.trigger_type,
        body.trigger_config,
        after=datetime.now(timezone.utc),
    )
    goal = Goal(
        assignee_agent_id=agent_id,
        owner_id=identity.id,
        report_room_id=body.report_room_id,
        title=body.title,
        spec=body.spec,
        status="active",
        trigger_type=body.trigger_type,
        trigger_config=body.trigger_config,
        materialize=body.materialize,
        consecutive_failures=0,
        next_run_at=next_run_at,
        created_at=now,
        updated_at=now,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return _to_out(goal)


@router.get(
    "/api/v1/agents/{agent_id}/goals", response_model=list[GoalOut]
)
async def list_goals_for_agent(
    agent_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Goal)
            .where(Goal.assignee_agent_id == agent_id)
            .order_by(Goal.created_at)
        )
    ).scalars().all()
    return [_to_out(g) for g in rows]


@router.get(
    "/api/v1/rooms/{room_id}/goals", response_model=list[GoalOut]
)
async def list_goals_for_room(
    room_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Goal)
            .where(Goal.report_room_id == room_id)
            .order_by(Goal.created_at)
        )
    ).scalars().all()
    return [_to_out(g) for g in rows]


@router.get("/api/v1/goals/{goal_id}", response_model=GoalOut)
async def get_goal(
    goal_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    goal = await _load_goal_owned(db, goal_id, identity)
    return _to_out(goal)


@router.patch("/api/v1/goals/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: str,
    body: GoalUpdate,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    goal = await _load_goal_owned(db, goal_id, identity)

    new_trigger_type = body.trigger_type or goal.trigger_type
    new_trigger_config = (
        body.trigger_config if body.trigger_config is not None else goal.trigger_config
    )
    if body.trigger_type is not None or body.trigger_config is not None:
        try:
            validate_trigger_config(new_trigger_type, new_trigger_config)
        except InvalidTriggerConfig as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.report_room_id is not None or "report_room_id" in body.model_fields_set:
        await _ensure_agent_in_room(
            db, goal.assignee_agent_id, body.report_room_id
        )

    if body.title is not None:
        goal.title = body.title
    if body.spec is not None:
        goal.spec = body.spec
    if body.trigger_type is not None:
        goal.trigger_type = body.trigger_type
    if body.trigger_config is not None:
        goal.trigger_config = body.trigger_config
    if body.materialize is not None:
        goal.materialize = body.materialize
    if "report_room_id" in body.model_fields_set:
        goal.report_room_id = body.report_room_id
    if body.status is not None:
        goal.status = body.status

    # If trigger config changed, recompute next_run_at so the
    # scheduler doesn't keep firing on the stale schedule.
    if body.trigger_type is not None or body.trigger_config is not None:
        goal.next_run_at = compute_next_run_at(
            goal.trigger_type,
            goal.trigger_config,
            after=datetime.now(timezone.utc),
        )

    await db.commit()
    await db.refresh(goal)
    return _to_out(goal)


@router.delete("/api/v1/goals/{goal_id}", status_code=204)
async def delete_goal(
    goal_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    goal = await _load_goal_owned(db, goal_id, identity)
    await db.delete(goal)
    await db.commit()


def _manual_run_idempotency_key(goal: Goal, now: datetime) -> str:
    """Deterministic dedup key for a Run-now fire (#449).

    - scheduled goal (``next_run_at`` set): key on the current slot so
      a manual fire that races the scheduler's claim of the same slot
      collapses to one Task.
    - manual goal (``next_run_at`` is NULL): bucket by the minute so
      repeated Run-now clicks within the same minute dedup, but a
      genuine re-run a minute later gets its own Task.
    """
    if goal.next_run_at is not None:
        return f"{goal.id}:{int(goal.next_run_at.timestamp())}"
    minute_bucket = int(now.replace(second=0, microsecond=0).timestamp())
    return f"{goal.id}:manual:{minute_bucket}"


@router.post("/api/v1/goals/{goal_id}/run", response_model=GoalOut)
async def manual_run_goal(
    goal_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Fire one execution now, regardless of schedule. Convenient for
    "run-once" type goals (``trigger_type='manual'``) and for owners
    who want to verify a freshly-edited spec without waiting for the
    next cron tick.

    Idempotent (#449): the fire stamps a deterministic
    ``idempotency_key``. A duplicate Run-now of the same slot/minute
    (or one racing the scheduler) hits the ``uq_tasks_idempotency_key``
    UNIQUE index → we collapse the IntegrityError into the existing
    goal state and return 200 instead of creating a second Task.
    Note a Run-now no longer advances ``next_run_at`` — the schedule
    is owned solely by the scheduler's CAS claim now."""
    goal = await _load_goal_owned(db, goal_id, identity)
    now = datetime.now(timezone.utc)
    idempotency_key = _manual_run_idempotency_key(goal, now)
    try:
        await trigger_goal(
            db,
            goal,
            trigger_source="manual",
            idempotency_key=idempotency_key,
        )
        await db.commit()
    except GoalExecutionError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError:
        # Duplicate slot/minute — a concurrent fire already created the
        # Task for this key. Roll back, re-fetch the goal, return the
        # idempotent 200 (the existing Task is unchanged).
        await db.rollback()
        goal = await db.get(Goal, goal_id)
        if goal is None:  # pragma: no cover — vanished mid-flight
            raise HTTPException(status_code=404, detail="goal not found")
        return _to_out(goal)
    await db.refresh(goal)
    return _to_out(goal)


@router.post(
    "/api/v1/goals/{goal_id}/scheduled-events", response_model=ScheduledEventOut
)
async def deliver_scheduled_event(
    goal_id: str,
    body: ScheduledEventIn,
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Deliver an authoritative due slot, or read its durable consumed Task."""
    await _load_scheduled_goal_owned(db, goal_id, identity)
    slot = body.scheduled_for.astimezone(UTC)
    now = datetime.now(UTC)
    scheduler = GoalScheduler(
        request.app.state.session_factory,
        manager=getattr(request.app.state, "connection_manager", None),
    )
    try:
        claim = await scheduler._claim_and_fire(db, goal_id, now, scheduled_for=slot)
    except ScheduledEventError as exc:
        await db.rollback()
        status = 422 if exc.code in {
            "SCHEDULED_TIMEZONE_REQUIRED", "SCHEDULED_SLOT_FUTURE",
        } else 404 if exc.code == "GOAL_NOT_FOUND" else 409
        raise HTTPException(status_code=status, detail=exc.code) from exc
    except (GoalExecutionError, InvalidTriggerConfig):
        await db.rollback()
        raise HTTPException(status_code=409, detail="SCHEDULED_GOAL_UNAVAILABLE") from None
    if claim is None:  # pragma: no cover — strict supplied slots return a decision
        raise HTTPException(status_code=409, detail="SCHEDULED_SLOT_CHANGED")
    task = claim.task
    return ScheduledEventOut(
        goal_id=goal_id,
        scheduled_for=slot.isoformat(),
        status="waiting_overlap" if claim.waiting_overlap else "duplicate" if claim.duplicate else "accepted",
        task_id=task.id if task else None,
        duplicate=claim.duplicate,
        schedule_context=task.schedule_context if task else None,
    )


@router.post("/api/v1/goals/{goal_id}/pause", response_model=GoalOut)
async def pause_goal(
    goal_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    goal = await _load_goal_owned(db, goal_id, identity)
    goal.status = "paused"
    await db.commit()
    await db.refresh(goal)
    return _to_out(goal)


@router.post("/api/v1/goals/{goal_id}/resume", response_model=GoalOut)
async def resume_goal(
    goal_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    goal = await _load_goal_owned(db, goal_id, identity)
    goal.status = "active"
    goal.consecutive_failures = 0
    if goal.trigger_type != "manual":
        goal.next_run_at = compute_next_run_at(
            goal.trigger_type,
            goal.trigger_config,
            after=datetime.now(timezone.utc),
        )
    await db.commit()
    await db.refresh(goal)
    return _to_out(goal)
