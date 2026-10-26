"""Campaign service (#930).

The single boundary that routers, MCP tools, the scheduler and the hooks
in existing code call. This module holds:

- ``transition(status, event)`` — the pure §3.3 state table,
- ``apply_transition`` — the CAS-guarded write of a transition plus its
  event row,
- ``record_event`` — idempotent history rows (unique ``event_key``),
- ``create_campaign``,
- time helpers (``cycle_closed_at``, ``next_cycle_at``) with an
  injectable clock, because the sweeper and Task API stamp the real clock
  while tests and the scheduler may run on another one,
- ``cycle_execution_limits`` / ``attach_cycle_execution`` used by
  ``begin_execution`` for a cycle root Task.

Later phases plug in here: ``on_execution_terminal``, ``on_eval_result``
and ``on_cycle_task_closed`` close the open attempt and drive the state
through ``apply_transition``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.campaigns.policy import (
    MIN_GAP_SECONDS,
    OPEN_STATUSES,
    TERMINAL_STATUSES,
    CampaignPolicyError,
    usable_tokens,
    validate_campaign_spec,
)
from anygarden.db.models import (
    Campaign,
    CampaignAttempt,
    CampaignEvent,
    Participant,
    Room,
    Task,
)

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ── State table (§3.3) ──────────────────────────────────────────────


class IllegalTransition(ValueError):
    """The event is not allowed in the campaign's current status."""

    def __init__(self, status: str, event: str) -> None:
        super().__init__(f"campaign event {event!r} is not allowed in status {status!r}")
        self.status = status
        self.event = event


_TRANSITIONS: dict[tuple[str, str], str] = {
    # People pause and resume; an agent report, eval failures or a pin
    # mismatch block. Resume is the way out of blocked and plateau.
    ("active", "pause"): "paused",
    ("paused", "resume"): "active",
    ("blocked", "resume"): "active",
    ("plateau", "resume"): "active",
    # A cycle may still finish (and be judged) while the loop is paused.
    ("active", "block"): "blocked",
    ("paused", "block"): "blocked",
    ("active", "plateau"): "plateau",
    # A dev score at the target only asks a person to confirm (CMP-06);
    # a trusted score at the target achieves directly (CMP-07).
    ("active", "target_reached"): "awaiting_confirmation",
    ("paused", "target_reached"): "awaiting_confirmation",
    ("awaiting_confirmation", "confirm"): "achieved",
    ("awaiting_confirmation", "reject"): "active",
    ("active", "trusted_target_reached"): "achieved",
    ("paused", "trusted_target_reached"): "achieved",
    ("plateau", "trusted_target_reached"): "achieved",
    ("awaiting_confirmation", "trusted_target_reached"): "achieved",
}
for _status in OPEN_STATUSES:
    _TRANSITIONS[(_status, "deadline")] = "deadline_reached"
    _TRANSITIONS[(_status, "budget_exhausted")] = "budget_exhausted"
    _TRANSITIONS[(_status, "stop")] = "stopped"
    # Edits and extensions change criteria, not the status.
    _TRANSITIONS[(_status, "edit")] = _status
    _TRANSITIONS[(_status, "extend")] = _status
del _status

EVENTS: frozenset[str] = frozenset(event for _, event in _TRANSITIONS)


def transition(status: str, event: str) -> str:
    """Return the status *event* leads to from *status*.

    Raises :class:`IllegalTransition` for any pair outside the table,
    including every event on a terminal status.
    """
    try:
        return _TRANSITIONS[(status, event)]
    except KeyError:
        raise IllegalTransition(status, event) from None


# ── Time ────────────────────────────────────────────────────────────


def cycle_closed_at(
    *,
    finished_at: datetime | None,
    opened_at: datetime,
    clock: Clock = utcnow,
) -> datetime:
    """When a cycle counts as closed: ``max(finished_at, opened_at)``.

    The sweeper and Task API stamp ``finished_at`` from the real clock, so
    it can precede an ``opened_at`` that came from an injected clock. A
    close never precedes its opening.
    """
    opened = _aware(opened_at)
    finished = _aware(finished_at) if finished_at is not None else _aware(clock())
    return max(finished, opened)


def next_cycle_at(closed_at: datetime, wake_at: datetime | None = None) -> datetime:
    """``max(closed + MIN_GAP_SECONDS, wake_at)``."""
    earliest = _aware(closed_at) + timedelta(seconds=MIN_GAP_SECONDS)
    if wake_at is None:
        return earliest
    return max(earliest, _aware(wake_at))


# ── Events ──────────────────────────────────────────────────────────


async def record_event(
    db: AsyncSession,
    campaign_id: str,
    *,
    key: str,
    type: str,
    details: dict[str, Any] | None = None,
    actor: str | None = None,
    clock: Clock = utcnow,
) -> CampaignEvent | None:
    """Append one history row. Returns ``None`` when *key* already exists,
    so a replayed hook records its event once."""
    existing = await db.scalar(
        select(CampaignEvent.id).where(CampaignEvent.event_key == key)
    )
    if existing is not None:
        return None
    event = CampaignEvent(
        campaign_id=campaign_id,
        event_key=key,
        type=type,
        details=details,
        actor=actor,
        created_at=clock(),
    )
    try:
        async with db.begin_nested():
            db.add(event)
            await db.flush()
    except IntegrityError:
        return None
    return event


# ── State writes ────────────────────────────────────────────────────


async def cas_update(
    db: AsyncSession,
    campaign: Campaign,
    *,
    clock: Clock = utcnow,
    expected_revision: int | None = None,
    **values: Any,
) -> bool:
    """Write loop state iff ``state_revision`` is still the one we saw.

    Bumps the revision and mirrors the written values onto the in-session
    object. Returns ``False`` when another writer got there first; the
    caller then drops its plan for this campaign.
    """
    seen = campaign.state_revision if expected_revision is None else expected_revision
    now = clock()
    result = await db.execute(
        update(Campaign)
        .where(Campaign.id == campaign.id, Campaign.state_revision == seen)
        .values(state_revision=seen + 1, updated_at=now, **values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    for name, value in values.items():
        setattr(campaign, name, value)
    campaign.state_revision = seen + 1
    campaign.updated_at = now
    return True


async def apply_transition(
    db: AsyncSession,
    campaign: Campaign,
    event: str,
    *,
    actor: str,
    clock: Clock = utcnow,
    details: dict[str, Any] | None = None,
    key: str | None = None,
    expected_revision: int | None = None,
    **values: Any,
) -> bool:
    """Move *campaign* through *event* under CAS and record the change.

    ``values`` are extra columns written in the same UPDATE (e.g.
    ``stop_reason``). Raises :class:`IllegalTransition` before touching the
    DB; returns ``False`` if the CAS lost.
    """
    previous = campaign.status
    new_status = transition(previous, event)
    if not await cas_update(
        db, campaign, clock=clock, expected_revision=expected_revision,
        status=new_status, **values,
    ):
        return False
    await record_event(
        db,
        campaign.id,
        key=key or f"campaign:{campaign.id}:rev:{campaign.state_revision}",
        type="status_changed",
        details={"event": event, "from": previous, "to": new_status, **(details or {})},
        actor=actor,
        clock=clock,
    )
    return True


# ── Creation ────────────────────────────────────────────────────────


async def create_campaign(
    db: AsyncSession,
    *,
    owner_id: str | None,
    room: Room,
    title: str,
    purpose: str,
    mode: str,
    metric: dict,
    evaluator: dict,
    budget: dict,
    deadline_at: datetime | None = None,
    trusted_eval: str = "none",
    clock: Clock = utcnow,
) -> Campaign:
    """Create an active campaign run by the room's lead. Caller commits.

    Cycles run as project executions, which only the project room's lead
    (representative) may begin — so the campaign is bound to that agent
    at creation and must be a participant of the room.
    """
    now = clock()
    spec = validate_campaign_spec(
        title=title, purpose=purpose, mode=mode, metric=metric,
        evaluator=evaluator, budget=budget, deadline_at=deadline_at,
        trusted_eval=trusted_eval, now=now,
    )
    if room.archived_at is not None:
        raise CampaignPolicyError("ROOM_ARCHIVED", "The room is archived")
    agent_id = room.representative_agent_id
    if room.project_id is None or room.is_dm or agent_id is None:
        raise CampaignPolicyError(
            "ROOM_LEAD_REQUIRED", "A campaign runs in a project room with a lead agent"
        )
    if await find_agent_participant(db, room_id=room.id, agent_id=agent_id) is None:
        raise CampaignPolicyError(
            "ROOM_LEAD_REQUIRED", "The room's lead agent is not a participant"
        )
    campaign = Campaign(
        owner_id=owner_id,
        room_id=room.id,
        agent_id=agent_id,
        title=spec.title,
        purpose=spec.purpose,
        mode=spec.mode,
        metric=spec.metric,
        evaluator=spec.evaluator,
        trusted_eval=spec.trusted_eval,
        budget=spec.budget,
        deadline_at=spec.deadline_at,
        status="active",
        next_cycle_at=now,
        state_revision=0,
        created_at=now,
        updated_at=now,
    )
    db.add(campaign)
    await db.flush()
    await record_event(
        db,
        campaign.id,
        key=f"campaign:{campaign.id}:created",
        type="created",
        details={"mode": spec.mode, "agent_id": agent_id},
        actor=f"user:{owner_id}" if owner_id else "system",
        clock=clock,
    )
    return campaign


async def find_agent_participant(
    db: AsyncSession, *, room_id: str, agent_id: str
) -> Participant | None:
    return await db.scalar(
        select(Participant).where(
            Participant.room_id == room_id, Participant.agent_id == agent_id
        )
    )


# ── Budget ──────────────────────────────────────────────────────────


def remaining_cycle_tokens(campaign: Campaign) -> int:
    """Tokens cycles may still spend (the retrospective reserve excluded)."""
    return max(0, usable_tokens(campaign.budget) - int(campaign.tokens_used or 0))


# ── Cycle executions (begin_execution) ──────────────────────────────


async def open_attempt_for_task(db: AsyncSession, task: Task) -> CampaignAttempt | None:
    """The still-open attempt whose cycle Task is *task*."""
    if task.campaign_id is None:
        return None
    return await db.scalar(
        select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == task.campaign_id,
            CampaignAttempt.task_id == task.id,
            CampaignAttempt.closed_at.is_(None),
        )
    )


def _limit_error(code: str, message: str) -> CampaignPolicyError:
    return CampaignPolicyError(code, message)


async def cycle_execution_limits(
    db: AsyncSession,
    *,
    root: Task,
    limits: dict[str, Any],
    deadline_at: datetime | None,
    clock: Clock = utcnow,
) -> tuple[Campaign, dict[str, Any], datetime | None]:
    """Clamp a cycle execution's limits to what the campaign has left (CMP-03).

    ``max_total_tokens`` becomes ``min(requested, remaining budget minus
    the retro reserve)`` and ``deadline_at`` ``min(requested, campaign
    deadline)``. Raises :class:`CampaignPolicyError` when the Task is not
    the campaign's open cycle or nothing is left to spend.
    """
    campaign = await db.get(Campaign, root.campaign_id)
    if campaign is None or campaign.status in TERMINAL_STATUSES:
        raise _limit_error("CAMPAIGN_NOT_ACTIVE", "This campaign has ended")
    if await open_attempt_for_task(db, root) is None:
        raise _limit_error(
            "CAMPAIGN_CYCLE_CLOSED", "This task is not the campaign's open cycle"
        )
    now = clock()
    remaining = remaining_cycle_tokens(campaign)
    if remaining <= 0:
        raise _limit_error("CAMPAIGN_BUDGET_EXHAUSTED", "The campaign budget is used up")
    clamped = dict(limits)
    requested = clamped.get("max_total_tokens")
    clamped["max_total_tokens"] = remaining if requested is None else min(int(requested), remaining)
    deadline = deadline_at
    if campaign.deadline_at is not None:
        campaign_deadline = _aware(campaign.deadline_at)
        if campaign_deadline <= now:
            raise _limit_error("CAMPAIGN_DEADLINE_PASSED", "The campaign deadline has passed")
        if deadline is None or campaign_deadline < deadline:
            deadline = campaign_deadline
        clamped["deadline_at"] = deadline.isoformat()
    return campaign, clamped, deadline


async def attach_cycle_execution(
    db: AsyncSession, *, root: Task, execution_id: str
) -> None:
    """Link the open attempt to the execution that runs it."""
    attempt = await open_attempt_for_task(db, root)
    if attempt is not None and attempt.execution_id is None:
        attempt.execution_id = execution_id
        await db.flush()


__all__ = [
    "EVENTS",
    "Clock",
    "IllegalTransition",
    "apply_transition",
    "attach_cycle_execution",
    "cas_update",
    "create_campaign",
    "cycle_closed_at",
    "cycle_execution_limits",
    "find_agent_participant",
    "next_cycle_at",
    "open_attempt_for_task",
    "record_event",
    "remaining_cycle_tokens",
    "transition",
    "utcnow",
]
