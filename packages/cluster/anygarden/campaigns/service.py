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
# #930 — an extension that raises the deadline or the budget past what
# ended the loop reopens it; ``extend_campaign`` checks that it does.
# ``achieved`` and ``stopped`` stay final.
_TRANSITIONS[("deadline_reached", "extend")] = "active"
_TRANSITIONS[("budget_exhausted", "extend")] = "active"

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
    from anygarden.campaigns.notify import mark

    mark(db, campaign_id=campaign.id)
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
    if new_status in RETRO_STATUSES and new_status != previous:
        # Phase 6 — the loop ended on its own terms: a retrospective cycle
        # is due (never after ``stopped``).
        values.setdefault("retro_due", new_status)
        if "final_attempt_id" not in values:
            # A closing cycle may become the best in this same write.
            values["final_attempt_id"] = await default_final_attempt_id(
                db, campaign, best_attempt_id=values.get("best_attempt_id", campaign.best_attempt_id)
            )
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


RETRO_STATUSES: frozenset[str] = frozenset({"achieved", "deadline_reached", "budget_exhausted"})
"""Ends that run a retrospective cycle (``stopped`` does not)."""


async def default_final_attempt_id(
    db: AsyncSession, campaign: Campaign, *, best_attempt_id: str | None = None
) -> str | None:
    """The final result when the loop ends (§3.3): the best trusted score
    among valid cycles, otherwise the best dev score (shown unverified).

    *best_attempt_id* overrides ``campaign.best_attempt_id`` when the
    transition that ends the loop also writes a new best."""
    from anygarden.campaigns.judge import gain

    rows = list(await db.scalars(
        select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == campaign.id,
            CampaignAttempt.kind == "cycle",
            CampaignAttempt.trusted_score.is_not(None),
        )
    ))
    direction = (campaign.metric or {}).get("direction", "maximize")
    best: CampaignAttempt | None = None
    for attempt in rows:
        if attempt.valid is False:
            continue
        if best is None or gain(attempt.trusted_score, best.trusted_score, direction) > 0:
            best = attempt
    if best is not None:
        return best.id
    return best_attempt_id if best_attempt_id is not None else campaign.best_attempt_id


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


# ── Extension ───────────────────────────────────────────────────────


async def extend_campaign(
    db: AsyncSession,
    campaign: Campaign,
    *,
    actor: str,
    deadline_at: datetime | None = None,
    max_total_tokens: int | None = None,
    clock: Clock = utcnow,
) -> bool:
    """Raise the deadline and/or the token budget. Caller commits.

    On an open campaign the status stays. A ``deadline_reached`` or
    ``budget_exhausted`` campaign returns to ``active`` only when the
    extension clears what ended it (a later deadline, a budget above what
    was used); otherwise :class:`CampaignPolicyError`. ``achieved`` and
    ``stopped`` refuse with :class:`IllegalTransition`. Returns ``False``
    when the CAS lost.
    """
    now = clock()
    transition(campaign.status, "extend")  # refuse final statuses early
    room = await db.get(Room, campaign.room_id)
    if room is None or room.archived_at is not None:
        # A ``deadline_reached`` campaign of a room archived later must not
        # come back to life in the archived room.
        raise CampaignPolicyError("ROOM_ARCHIVED", "The campaign's room is archived")
    if deadline_at is None and max_total_tokens is None:
        raise CampaignPolicyError("EXTEND_NOTHING", "Give a new deadline or token budget")
    values: dict[str, Any] = {}
    details: dict[str, Any] = {}
    deadline = _aware(campaign.deadline_at) if campaign.deadline_at is not None else None
    if deadline_at is not None:
        if deadline_at.tzinfo is None or deadline_at <= now or (
            deadline is not None and deadline_at <= deadline
        ):
            raise CampaignPolicyError(
                "INVALID_DEADLINE", "The new deadline must be later than now and the current one"
            )
        values["deadline_at"] = deadline_at
        details["deadline_at"] = {"from": deadline.isoformat() if deadline else None,
                                  "to": deadline_at.isoformat()}
        deadline = deadline_at
    budget = dict(campaign.budget)
    if max_total_tokens is not None:
        if (
            not isinstance(max_total_tokens, int) or isinstance(max_total_tokens, bool)
            or max_total_tokens <= int(budget["max_total_tokens"])
        ):
            raise CampaignPolicyError(
                "INVALID_BUDGET", "The new token budget must be larger than the current one"
            )
        details["max_total_tokens"] = {"from": budget["max_total_tokens"], "to": max_total_tokens}
        budget["max_total_tokens"] = max_total_tokens
        values["budget"] = budget
    if campaign.status in {"deadline_reached", "budget_exhausted"}:
        deadline_clear = deadline is None or deadline > now
        budget_clear = usable_tokens(budget) > int(campaign.tokens_used or 0)
        if not (deadline_clear and budget_clear):
            raise CampaignPolicyError(
                "EXTEND_DOES_NOT_REOPEN",
                "The extension does not clear both the deadline and the budget",
            )
        values["stop_reason"] = None
        values["final_attempt_id"] = None
        # The loop goes on; its next retrospective comes with the next end.
        values["retro_due"] = None
        current_next = campaign.next_cycle_at
        values["next_cycle_at"] = (
            now if current_next is None or _aware(current_next) < now else current_next
        )
    return await apply_transition(
        db, campaign, "extend", actor=actor, clock=clock, details=details, **values,
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


RETRO_MAX_SECONDS = 2 * 3600
"""Upper bound on a retrospective run (it may run past the deadline)."""


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
    the retro reserve)`` (a retrospective: ``remaining budget``, reserve
    included) and ``deadline_at`` ``min(requested, campaign deadline)``.
    The token limit is observed, not reserved: it is checked as native
    calls settle, so a call in flight can overshoot it. Raises :class:`CampaignPolicyError` when the Task is not
    the campaign's open cycle or nothing is left to spend.
    """
    campaign = await db.get(Campaign, root.campaign_id)
    attempt = await open_attempt_for_task(db, root)
    retrospective = attempt is not None and attempt.kind == "retrospective"
    if campaign is None or campaign.status == "stopped" or (
        campaign.status in TERMINAL_STATUSES and not retrospective
    ):
        raise _limit_error("CAMPAIGN_NOT_ACTIVE", "This campaign has ended")
    if attempt is None:
        raise _limit_error(
            "CAMPAIGN_CYCLE_CLOSED", "This task is not the campaign's open cycle"
        )
    now = clock()
    if retrospective:
        # Phase 6 — a retrospective may spend what the cycles left plus the
        # reserve (``retro_reserve_ratio``): ``budget - tokens_used``. Once
        # the usable budget is spent that is only what is left of the
        # reserve. It runs after the deadline too.
        remaining = max(0, int(campaign.budget["max_total_tokens"]) - int(campaign.tokens_used or 0))
        if remaining <= 0:
            raise _limit_error("CAMPAIGN_BUDGET_EXHAUSTED", "The campaign budget is used up")
        clamped = dict(limits)
        requested = clamped.get("max_total_tokens")
        clamped["max_total_tokens"] = (
            remaining if requested is None else min(int(requested), remaining)
        )
        retro_deadline = now + timedelta(seconds=RETRO_MAX_SECONDS)
        deadline = deadline_at if deadline_at is not None and deadline_at < retro_deadline else retro_deadline
        clamped["deadline_at"] = deadline.isoformat()
        return campaign, clamped, deadline
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
        from anygarden.campaigns.notify import mark

        mark(db, attempt_id=attempt.id)


__all__ = [
    "EVENTS",
    "RETRO_STATUSES",
    "Clock",
    "IllegalTransition",
    "apply_transition",
    "attach_cycle_execution",
    "cas_update",
    "create_campaign",
    "cycle_closed_at",
    "cycle_execution_limits",
    "default_final_attempt_id",
    "extend_campaign",
    "find_agent_participant",
    "next_cycle_at",
    "open_attempt_for_task",
    "record_event",
    "remaining_cycle_tokens",
    "transition",
    "utcnow",
]
