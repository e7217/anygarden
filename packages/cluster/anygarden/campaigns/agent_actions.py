"""What a campaign's agent may do during a cycle (#930 §3.5, Phase 5).

The MCP tools (``anygarden.mcp.campaign_tools``) are thin wrappers over
these functions. Every action runs inside the caller's transaction and
starts from :func:`current_cycle`, the common guard:

- the invocation carries the current delivered turn lease
  (``project_executions.service.authorize_turn``),
- the turn works on the campaign's **open** cycle: its Task is the cycle
  Task, or its execution is the cycle's execution,
- the calling agent is ``campaign.agent_id``.

The tools take no campaign or attempt ids from the model: the turn decides
which cycle they act on, so a stale turn of an earlier cycle is refused.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.campaigns.notify import mark
from anygarden.campaigns.policy import AGENT_OUTCOMES
from anygarden.campaigns.service import Clock, record_event, utcnow
from anygarden.campaigns.views import attempt_view
from anygarden.db.models import (
    AgentTurn,
    Campaign,
    CampaignAttempt,
    CampaignEvent,
    Task,
)

MIN_WAKEUP_SECONDS = 60
MAX_WAKEUP_SECONDS = 7 * 24 * 3600
MAX_ATTEMPTS_PAGE = 20
HYPOTHESIS_MAX = 4000
CHANGE_SUMMARY_MAX = 8000
LESSONS_MAX = 4000
ARTIFACT_REF_MAX = 1024
REASON_MAX = 4000
WAKE_REASON_MAX = 500
_EXCERPT_IN_LIST = 500

DIRECTIVE_EVENT_TYPES = frozenset({"edited", "status_changed"})


class CampaignToolError(ValueError):
    """A refused campaign tool call. ``code`` is stable for the model."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class CycleContext:
    campaign: Campaign
    attempt: CampaignAttempt
    turn: AgentTurn


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _text(value: Any, *, field: str, limit: int, required: bool = True) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise CampaignToolError("CAMPAIGN_INVALID_ARGUMENT", f"{field} is required")
        return None
    if not isinstance(value, str):
        raise CampaignToolError("CAMPAIGN_INVALID_ARGUMENT", f"{field} must be a string")
    value = value.strip()
    if len(value) > limit:
        raise CampaignToolError(
            "CAMPAIGN_INVALID_ARGUMENT", f"{field} is longer than {limit} characters"
        )
    return value


async def current_cycle(db: AsyncSession, *, agent_id: str, proof) -> CycleContext:
    """The common guard of every campaign tool (see module docstring).

    Raises ``ExecutionConflict`` for a stale turn proof and
    :class:`CampaignToolError` when the turn is not on the open cycle.
    """
    from anygarden.project_executions.service import authorize_turn

    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    attempt: CampaignAttempt | None = None
    campaign_id: str | None = None
    if turn.task_id is not None:
        task = await db.get(Task, turn.task_id)
        if task is not None and task.campaign_id is not None:
            campaign_id = task.campaign_id
            attempt = await db.scalar(
                select(CampaignAttempt).where(
                    CampaignAttempt.campaign_id == task.campaign_id,
                    CampaignAttempt.task_id == task.id,
                    CampaignAttempt.closed_at.is_(None),
                ).execution_options(populate_existing=True)
            )
    if attempt is None and turn.execution_id is not None:
        rows = list(await db.scalars(
            select(CampaignAttempt)
            .where(CampaignAttempt.execution_id == turn.execution_id)
            .order_by(CampaignAttempt.cycle_no.desc())
            .execution_options(populate_existing=True)
        ))
        attempt = next((row for row in rows if row.closed_at is None), None)
        if rows:
            campaign_id = rows[0].campaign_id
    if attempt is None:
        if campaign_id is not None:
            campaign = await db.get(Campaign, campaign_id)
            state = campaign.status if campaign is not None else "deleted"
            raise CampaignToolError(
                "CAMPAIGN_CYCLE_CLOSED",
                f"This campaign cycle is closed (campaign status: {state}). "
                "Nothing more is recorded for it; end this turn.",
            )
        raise CampaignToolError(
            "CAMPAIGN_CYCLE_REQUIRED", "This turn is not working on a campaign cycle"
        )
    campaign = await db.get(Campaign, attempt.campaign_id, populate_existing=True)
    if campaign is None or campaign.agent_id != agent_id:
        raise CampaignToolError(
            "CAMPAIGN_AGENT_MISMATCH", "Only the campaign's agent can act on its cycles"
        )
    return CycleContext(campaign=campaign, attempt=attempt, turn=turn)


# ── Follow-up directives (decision 7) ───────────────────────────────


def _fmt(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _directive_text(event: CampaignEvent) -> str:
    details = event.details or {}
    if event.type == "edited":
        changes = details.get("changes") or {}
        parts = [
            f"{name}: {_fmt(change.get('from'))} → {_fmt(change.get('to'))}"
            if isinstance(change, dict) else str(name)
            for name, change in changes.items()
        ]
        return (
            "A person changed the campaign — " + "; ".join(parts)
            + ". It applies from now on: take it into account before you finish."
        )
    action = details.get("event")
    if action == "pause":
        return (
            "A person paused the campaign. Finish and record this cycle; no new cycle "
            "opens until it is resumed."
        )
    if action == "extend":
        parts = []
        if "deadline_at" in details:
            parts.append(f"deadline → {details['deadline_at'].get('to')}")
        if "max_total_tokens" in details:
            parts.append(f"token budget → {details['max_total_tokens'].get('to')}")
        return "A person extended the campaign (" + ", ".join(parts) + ")."
    if action == "resume":
        return "A person resumed the campaign."
    return f"The campaign status changed: {details.get('from')} → {details.get('to')}."


async def follow_up_directives(
    db: AsyncSession, campaign: Campaign, attempt: CampaignAttempt
) -> list[dict[str, Any]]:
    """Campaign edits and status changes that arrived since *attempt*
    opened (a person's action is stamped with the open cycle number; the
    timestamp catches the rest)."""
    rows = list(await db.scalars(
        select(CampaignEvent)
        .where(
            CampaignEvent.campaign_id == campaign.id,
            CampaignEvent.type.in_(tuple(DIRECTIVE_EVENT_TYPES)),
        )
        .order_by(CampaignEvent.created_at)
    ))
    opened = _aware(attempt.opened_at)
    out = []
    for event in rows:
        details = event.details or {}
        if event.type == "status_changed" and not str(event.actor or "").startswith("user:"):
            continue
        stamped = details.get("open_cycle_no")
        if stamped is not None:
            if stamped != attempt.cycle_no:
                continue
        elif event.created_at is None or _aware(event.created_at) < opened:
            continue
        out.append({
            "type": event.type,
            "at": _aware(event.created_at).isoformat() if event.created_at else None,
            "actor": event.actor,
            "instruction": _directive_text(event),
            "details": details,
        })
    return out


def _criteria(campaign: Campaign) -> dict[str, Any]:
    return {
        "status": campaign.status,
        "purpose": campaign.purpose,
        "metric": campaign.metric,
        "deadline_at": _aware(campaign.deadline_at).isoformat() if campaign.deadline_at else None,
        "budget": campaign.budget,
    }


# ── Actions ─────────────────────────────────────────────────────────


async def record_attempt(
    db: AsyncSession,
    ctx: CycleContext,
    *,
    outcome: Any,
    hypothesis: Any,
    change_summary: Any,
    artifact_ref: Any = None,
    lessons: Any = None,
    clock: Clock = utcnow,
) -> dict[str, Any]:
    """``record_campaign_attempt`` — once per cycle, before the evaluation."""
    campaign, attempt = ctx.campaign, ctx.attempt
    if attempt.kind == "retrospective":
        raise CampaignToolError(
            "CAMPAIGN_RETROSPECTIVE_CYCLE",
            "A retrospective records nothing here; use apply_campaign_learning",
        )
    if attempt.recorded_at is not None:
        raise CampaignToolError(
            "CAMPAIGN_ATTEMPT_ALREADY_RECORDED", "This cycle's attempt is already recorded"
        )
    if attempt.eval_job_id is not None:
        raise CampaignToolError(
            "CAMPAIGN_EVAL_STARTED",
            "The evaluation of this cycle already started; the record is closed",
        )
    if outcome not in AGENT_OUTCOMES:
        raise CampaignToolError(
            "CAMPAIGN_INVALID_ARGUMENT", "outcome must be attempted or abandoned"
        )
    values = {
        "agent_outcome": outcome,
        "hypothesis": _text(hypothesis, field="hypothesis", limit=HYPOTHESIS_MAX),
        "change_summary": _text(change_summary, field="change_summary", limit=CHANGE_SUMMARY_MAX),
        "artifact_ref": _text(artifact_ref, field="artifact_ref", limit=ARTIFACT_REF_MAX,
                              required=False),
        "lessons": _text(lessons, field="lessons", limit=LESSONS_MAX, required=False),
        "recorded_at": clock(),
    }
    result = await db.execute(
        update(CampaignAttempt)
        .where(
            CampaignAttempt.id == attempt.id,
            CampaignAttempt.recorded_at.is_(None),
            CampaignAttempt.closed_at.is_(None),
            CampaignAttempt.eval_job_id.is_(None),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise CampaignToolError(
            "CAMPAIGN_ATTEMPT_ALREADY_RECORDED",
            "This cycle's attempt was recorded or closed meanwhile",
        )
    for name, value in values.items():
        setattr(attempt, name, value)
    mark(db, attempt_id=attempt.id)
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:cycle:{attempt.cycle_no}:recorded",
        type="attempt_recorded",
        details={"cycle_no": attempt.cycle_no, "agent_outcome": outcome},
        actor=f"agent:{campaign.agent_id}", clock=clock,
    )
    directives = await follow_up_directives(db, campaign, attempt)
    return {
        "campaign_id": campaign.id,
        "cycle_no": attempt.cycle_no,
        "recorded": True,
        "campaign": _criteria(campaign),
        "follow_up": directives,
        "note": (
            "Recorded. The system evaluates the run after you finish it with "
            "complete_project_execution; the score is not yours to report. "
            + ("Read follow_up before you finish." if directives else "No follow-up instructions.")
        ),
    }


def _closed_meanwhile() -> CampaignToolError:
    return CampaignToolError(
        "CAMPAIGN_CYCLE_CLOSED",
        "This campaign cycle closed meanwhile. Nothing more is recorded for it; end this turn.",
    )


def _parse_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CampaignToolError("CAMPAIGN_INVALID_ARGUMENT", "at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise CampaignToolError(
            "CAMPAIGN_INVALID_ARGUMENT", "at must be an ISO timestamp"
        ) from None
    if parsed.tzinfo is None:
        raise CampaignToolError("CAMPAIGN_INVALID_ARGUMENT", "at needs a timezone")
    return parsed


async def schedule_wakeup(
    db: AsyncSession,
    ctx: CycleContext,
    *,
    delay_seconds: Any = None,
    at: Any = None,
    reason: Any,
    clock: Clock = utcnow,
) -> dict[str, Any]:
    """``schedule_campaign_wakeup`` — the next cycle opens no earlier than
    the wake time (``next_cycle_at = max(close + gap, wake_at)``). The last
    call in a cycle wins."""
    from anygarden.campaigns.policy import MIN_GAP_SECONDS

    campaign, attempt = ctx.campaign, ctx.attempt
    if (delay_seconds is None) == (at is None):
        raise CampaignToolError(
            "CAMPAIGN_INVALID_ARGUMENT", "Give exactly one of delay_seconds or at"
        )
    now = clock()
    if delay_seconds is not None:
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int | float):
            raise CampaignToolError("CAMPAIGN_INVALID_ARGUMENT", "delay_seconds must be a number")
        delay = float(delay_seconds)
        if not math.isfinite(delay):
            raise CampaignToolError(
                "CAMPAIGN_INVALID_ARGUMENT", "delay_seconds must be a finite number"
            )
        # Check the range before building a timedelta: a huge value overflows.
        wake_at = (
            now + timedelta(seconds=delay)
            if MIN_WAKEUP_SECONDS <= delay <= MAX_WAKEUP_SECONDS else None
        )
    else:
        wake_at = _parse_at(at)
        delay = (wake_at - now).total_seconds()
    if wake_at is None or not MIN_WAKEUP_SECONDS <= delay <= MAX_WAKEUP_SECONDS:
        raise CampaignToolError(
            "CAMPAIGN_WAKEUP_OUT_OF_RANGE",
            f"The wake-up must be {MIN_WAKEUP_SECONDS}s to {MAX_WAKEUP_SECONDS // 86400} days away",
        )
    if campaign.deadline_at is not None and wake_at >= _aware(campaign.deadline_at):
        raise CampaignToolError(
            "CAMPAIGN_WAKEUP_AFTER_DEADLINE", "The wake-up must be before the campaign deadline"
        )
    text = _text(reason, field="reason", limit=WAKE_REASON_MAX)
    result = await db.execute(
        update(CampaignAttempt)
        .where(CampaignAttempt.id == attempt.id, CampaignAttempt.closed_at.is_(None))
        .values(wake_at=wake_at, wake_reason=text)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise _closed_meanwhile()
    attempt.wake_at, attempt.wake_reason = wake_at, text
    mark(db, attempt_id=attempt.id)
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:cycle:{attempt.cycle_no}:wakeup:{uuid4().hex}",
        type="wakeup_scheduled",
        details={"cycle_no": attempt.cycle_no, "wake_at": wake_at.isoformat(), "reason": text},
        actor=f"agent:{campaign.agent_id}", clock=clock,
    )
    return {
        "campaign_id": campaign.id,
        "cycle_no": attempt.cycle_no,
        "wake_at": wake_at.isoformat(),
        "note": (
            f"Scheduled. The next cycle opens no earlier than this, and at least "
            f"{MIN_GAP_SECONDS}s after this cycle closes. A later call replaces it. "
            "Finish this cycle as usual."
        ),
    }


async def report_blocked(
    db: AsyncSession,
    ctx: CycleContext,
    *,
    reason: Any,
    unblock_hint: Any,
    clock: Clock = utcnow,
) -> dict[str, Any]:
    """``report_campaign_blocked`` — recorded on the open cycle and applied
    when it closes (``lifecycle._after_close``).

    Applying it at close rather than at once keeps the one-open-cycle rule
    simple: the cycle still ends (and is judged) normally, and a verdict
    that already moves the campaign (target reached, budget used up, an
    evaluator problem) takes precedence over the agent's report.
    """
    campaign, attempt = ctx.campaign, ctx.attempt
    reason_text = _text(reason, field="reason", limit=REASON_MAX)
    hint = _text(unblock_hint, field="unblock_hint", limit=REASON_MAX)
    result = await db.execute(
        update(CampaignAttempt)
        .where(CampaignAttempt.id == attempt.id, CampaignAttempt.closed_at.is_(None))
        .values(block_reason=reason_text, unblock_hint=hint)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise _closed_meanwhile()
    attempt.block_reason, attempt.unblock_hint = reason_text, hint
    mark(db, attempt_id=attempt.id)
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:cycle:{attempt.cycle_no}:block_reported:{uuid4().hex}",
        type="block_reported",
        details={"cycle_no": attempt.cycle_no, "reason": reason_text, "unblock_hint": hint},
        actor=f"agent:{campaign.agent_id}", clock=clock,
    )
    return {
        "campaign_id": campaign.id,
        "cycle_no": attempt.cycle_no,
        "blocked_after_cycle": True,
        "note": (
            "Recorded. The campaign becomes blocked when this cycle closes and waits for a "
            "person. Record the attempt if you have not, finish the run, then end your turn."
        ),
    }


CONFIRM_ADVICE = frozenset({"confirm", "hold"})


async def advise_confirmation(
    db: AsyncSession,
    *,
    agent_id: str,
    proof,
    advice: Any,
    reason: Any,
    clock: Clock = utcnow,
) -> dict[str, Any]:
    """``advise_goal_confirmation`` — the lead's advice on confirming a goal
    that reached its target (#969), shown next to the confirm button.

    Only the lead's confirmation report turn may give it, and only while the
    goal still waits in the state that turn reports on; a later call in the
    same turn replaces the earlier advice.
    """
    from anygarden.campaigns.report import report_key
    from anygarden.campaigns.views import CONFIRM_ADVICE_EVENT
    from anygarden.project_executions.service import authorize_turn

    if advice not in CONFIRM_ADVICE:
        raise CampaignToolError("CAMPAIGN_INVALID_ARGUMENT", "advice must be confirm or hold")
    reason_text = _text(reason, field="reason", limit=REASON_MAX)
    turn = await authorize_turn(db, agent_id=agent_id, proof=proof)
    key = turn.idempotency_key or ""
    parts = key.split(":")
    campaign = (
        await db.get(Campaign, parts[1], populate_existing=True)
        if len(parts) == 4 and parts[0] == "goal-report" and parts[2] == "confirmation"
        else None
    )
    if campaign is None or campaign.agent_id != agent_id:
        raise CampaignToolError(
            "CAMPAIGN_REPORT_TURN_REQUIRED",
            "Only the lead's report turn for a goal that waits for confirmation can give "
            "confirm advice.",
        )
    if campaign.status != "awaiting_confirmation" or key != report_key(campaign, "confirmation"):
        raise CampaignToolError(
            "CAMPAIGN_CONFIRMATION_DECIDED",
            f"The goal no longer waits for this confirmation (status: {campaign.status}). "
            "Nothing was recorded.",
        )
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:confirm_advice:{uuid4().hex}",
        type=CONFIRM_ADVICE_EVENT,
        details={"advice": advice, "reason": reason_text,
                 "state_revision": campaign.state_revision},
        actor=f"agent:{agent_id}", clock=clock,
    )
    mark(db, campaign_id=campaign.id)
    return {
        "campaign_id": campaign.id,
        "advice": advice,
        "note": "Recorded. It is shown next to the confirm button; finish your report reply.",
    }


async def read_attempts(
    db: AsyncSession, ctx: CycleContext, *, offset: Any = 0, limit: Any = 10
) -> dict[str, Any]:
    """``read_campaign_attempts`` — the campaign's attempts, newest first."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise CampaignToolError("CAMPAIGN_INVALID_ARGUMENT", "offset must be ≥ 0")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ATTEMPTS_PAGE:
        raise CampaignToolError(
            "CAMPAIGN_INVALID_ARGUMENT", f"limit must be 1..{MAX_ATTEMPTS_PAGE}"
        )
    campaign = ctx.campaign
    total = int(await db.scalar(
        select(func.count()).select_from(CampaignAttempt)
        .where(CampaignAttempt.campaign_id == campaign.id)
    ) or 0)
    rows = list(await db.scalars(
        select(CampaignAttempt)
        .where(CampaignAttempt.campaign_id == campaign.id)
        .order_by(CampaignAttempt.cycle_no.desc())
        .offset(offset)
        .limit(limit)
    ))
    items = []
    for row in rows:
        view = attempt_view(row)
        if view.get("eval_excerpt"):
            view["eval_excerpt"] = view["eval_excerpt"][-_EXCERPT_IN_LIST:]
        items.append(view)
    next_offset = offset + len(rows)
    return {
        "campaign_id": campaign.id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset if next_offset < total else None,
        "attempts": items,
    }


__all__ = [
    "CampaignToolError",
    "CycleContext",
    "current_cycle",
    "follow_up_directives",
    "read_attempts",
    "record_attempt",
    "report_blocked",
    "schedule_wakeup",
]
