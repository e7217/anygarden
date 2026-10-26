"""What people do to a campaign (#930 §3.3, §3.8, Phase 7).

The REST router (``api/v1/campaigns.py``) is a thin wrapper over these
functions. Each runs in the caller's transaction and never commits.
Every action is recorded as a campaign event; actions taken while a
cycle is open carry ``open_cycle_no`` so the cycle's agent gets them back
as follow-up directives from ``record_campaign_attempt``.

Raises :class:`CampaignPolicyError` (bad input, 422) or
:class:`IllegalTransition` / :class:`CampaignConflict` (not now, 409).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.campaigns.judge import reaches_target
from anygarden.campaigns.notify import mark
from anygarden.campaigns.policy import CampaignPolicyError, validate_campaign_spec
from anygarden.campaigns.service import (
    RETRO_STATUSES,
    Clock,
    IllegalTransition,
    apply_transition,
    cas_update,
    record_event,
    transition,
    utcnow,
)
from anygarden.db.models import Campaign, CampaignAttempt

_TEXT_IN_EVENT = 200


class CampaignConflict(ValueError):
    """The campaign changed meanwhile or the action does not apply now."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _aware(value: datetime | None) -> datetime | None:
    from datetime import UTC

    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value is not None else None


async def open_cycle_no(db: AsyncSession, campaign: Campaign) -> int | None:
    return await db.scalar(
        select(CampaignAttempt.cycle_no).where(
            CampaignAttempt.campaign_id == campaign.id,
            CampaignAttempt.closed_at.is_(None),
        ).limit(1)
    )


def _lost() -> CampaignConflict:
    return CampaignConflict(
        "CAMPAIGN_CHANGED", "The campaign changed meanwhile; reload it and try again"
    )


async def _transition(
    db: AsyncSession, campaign: Campaign, event: str, *, actor: str, clock: Clock,
    details: dict[str, Any] | None = None, **values: Any,
) -> None:
    details = {**(details or {}), "open_cycle_no": await open_cycle_no(db, campaign)}
    if not await apply_transition(
        db, campaign, event, actor=actor, clock=clock, details=details, **values
    ):
        raise _lost()


# ── Edit (PATCH) ────────────────────────────────────────────────────


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _TEXT_IN_EVENT:
        return value[: _TEXT_IN_EVENT - 1] + "…"
    return value


async def edit_campaign(
    db: AsyncSession,
    campaign: Campaign,
    *,
    actor: str,
    changes: dict[str, Any],
    clock: Clock = utcnow,
) -> dict[str, dict[str, Any]]:
    """Change purpose, criteria, deadline, budget or evaluator.

    *changes* holds only the fields the caller sent: ``title``,
    ``purpose``, ``metric`` (``target`` / ``min_delta``), ``deadline_at``
    (``None`` clears it), ``budget`` (``max_total_tokens`` /
    ``retro_reserve_ratio``), ``evaluator`` (replaced whole; its pins are
    re-recorded at the next evaluation) and ``trusted_eval``. Mode, metric
    name and direction are fixed: changing them would make earlier scores
    incomparable. Applies from the next cycle; an open cycle gets it as a
    follow-up directive. Returns ``{field: {from, to}}`` (empty: no-op).
    """
    transition(campaign.status, "edit")  # open statuses only
    now = clock()
    metric = dict(campaign.metric)
    for key, value in (changes.get("metric") or {}).items():
        metric[key] = value
    budget = dict(campaign.budget)
    for key, value in (changes.get("budget") or {}).items():
        budget[key] = value
    deadline_changed = "deadline_at" in changes
    deadline = changes["deadline_at"] if deadline_changed else _aware(campaign.deadline_at)
    check_now = now
    if not deadline_changed and deadline is not None and deadline <= now:
        # The tick will end it; an unrelated edit is still validated.
        check_now = deadline - timedelta(seconds=1)
    spec = validate_campaign_spec(
        title=changes.get("title", campaign.title),
        purpose=changes.get("purpose", campaign.purpose),
        mode=campaign.mode,
        metric=metric,
        evaluator=changes.get("evaluator", campaign.evaluator),
        budget=budget,
        deadline_at=deadline,
        trusted_eval=changes.get("trusted_eval", campaign.trusted_eval),
        now=check_now,
    )
    new = {
        "title": spec.title, "purpose": spec.purpose, "metric": spec.metric,
        "evaluator": spec.evaluator, "budget": spec.budget, "deadline_at": spec.deadline_at,
        "trusted_eval": spec.trusted_eval,
    }
    diff: dict[str, dict[str, Any]] = {}
    values: dict[str, Any] = {}
    for field in ("title", "purpose", "trusted_eval"):
        if new[field] != getattr(campaign, field):
            diff[field] = {"from": _clip(getattr(campaign, field)), "to": _clip(new[field])}
            values[field] = new[field]
    for field in ("metric", "budget"):
        old = getattr(campaign, field) or {}
        for key, value in new[field].items():
            if old.get(key) != value:
                diff[f"{field}.{key}"] = {"from": old.get(key), "to": value}
        if new[field] != old:
            values[field] = new[field]
    if new["evaluator"] != campaign.evaluator:
        diff["evaluator"] = {"from": campaign.evaluator, "to": new["evaluator"]}
        values["evaluator"] = new["evaluator"]
        # A new evaluator is pinned afresh at its first evaluation — this is
        # how a person clears a pin mismatch.
        values["evaluator_pins"] = None
    if _aware(campaign.deadline_at) != new["deadline_at"]:
        diff["deadline_at"] = {"from": _iso(campaign.deadline_at), "to": _iso(new["deadline_at"])}
        values["deadline_at"] = new["deadline_at"]
    if not diff:
        return diff
    cycle_no = await open_cycle_no(db, campaign)
    if not await cas_update(db, campaign, clock=clock, **values):
        raise _lost()
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:edit:{campaign.state_revision}",
        type="edited",
        details={"changes": diff, "open_cycle_no": cycle_no},
        actor=actor, clock=clock,
    )
    return diff


# ── Status actions ──────────────────────────────────────────────────


async def pause_campaign(db, campaign, *, actor: str, clock: Clock = utcnow) -> None:
    await _transition(db, campaign, "pause", actor=actor, clock=clock)


async def resume_campaign(db, campaign, *, actor: str, clock: Clock = utcnow) -> None:
    """Back to ``active`` from paused, blocked or plateau. Leaving
    ``blocked`` resets the evaluation-failure streak and leaving
    ``plateau`` the no-improvement counter, so the next cycle gets a fair
    chance instead of re-triggering at once."""
    values: dict[str, Any] = {"status_detail": None}
    if campaign.status == "blocked":
        values["consecutive_eval_failures"] = 0
    if campaign.status == "plateau":
        values["cycles_since_improvement"] = 0
    await _transition(db, campaign, "resume", actor=actor, clock=clock, **values)


async def confirm_campaign(db, campaign, *, actor: str, clock: Clock = utcnow) -> None:
    """A person accepts the dev score that reached the target (CMP-06)."""
    await _transition(db, campaign, "confirm", actor=actor, clock=clock, stop_reason="achieved",
                      final_attempt_id=campaign.best_attempt_id)


async def reject_campaign(
    db, campaign, *, actor: str, reason: str | None = None, clock: Clock = utcnow
) -> None:
    """A person rejects the dev result; the loop goes on."""
    values: dict[str, Any] = {}
    now = clock()
    if campaign.next_cycle_at is None or _aware(campaign.next_cycle_at) < now:
        values["next_cycle_at"] = now
    await _transition(db, campaign, "reject", actor=actor, clock=clock,
                      details={"reason": _clip(reason)} if reason else None, **values)


async def _cycle_attempt(db: AsyncSession, campaign: Campaign, attempt_id: str) -> CampaignAttempt:
    attempt = await db.get(CampaignAttempt, attempt_id, populate_existing=True)
    if attempt is None or attempt.campaign_id != campaign.id:
        raise LookupError(attempt_id)
    return attempt


async def select_final(
    db, campaign, *, actor: str, attempt_id: str, clock: Clock = utcnow
) -> None:
    """Choose the campaign's final result after it ended (§3.3: by default
    the best trusted score, else the best dev score)."""
    if campaign.status not in RETRO_STATUSES:
        raise CampaignConflict(
            "CAMPAIGN_NOT_ENDED", "A final result is chosen once the campaign has ended"
        )
    attempt = await _cycle_attempt(db, campaign, attempt_id)
    if (
        attempt.kind != "cycle" or attempt.closed_at is None or attempt.valid is False
        or (attempt.dev_score is None and attempt.trusted_score is None)
    ):
        raise CampaignPolicyError(
            "ATTEMPT_NOT_SELECTABLE", "Choose a closed cycle with a valid score"
        )
    previous = campaign.final_attempt_id
    if not await cas_update(db, campaign, clock=clock, final_attempt_id=attempt.id):
        raise _lost()
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:final:{campaign.state_revision}",
        type="final_selected",
        details={"attempt_id": attempt.id, "cycle_no": attempt.cycle_no, "previous": previous},
        actor=actor, clock=clock,
    )


async def record_trusted_score(
    db, campaign, *, actor: str, attempt_id: str, score: float, clock: Clock = utcnow
) -> bool:
    """Store an externally verified score for a closed cycle (CMP-07).

    In target mode a valid attempt whose trusted score reaches the target
    achieves the campaign. Returns whether it did.
    """
    if isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(score):
        raise CampaignPolicyError("INVALID_SCORE", "score must be a finite number")
    attempt = await _cycle_attempt(db, campaign, attempt_id)
    if attempt.kind != "cycle" or attempt.closed_at is None:
        raise CampaignPolicyError(
            "ATTEMPT_NOT_SCORABLE", "A trusted score belongs to a closed cycle"
        )
    now = clock()
    await db.execute(
        update(CampaignAttempt).where(CampaignAttempt.id == attempt.id)
        .values(trusted_score=float(score), trusted_at=now)
        .execution_options(synchronize_session=False)
    )
    attempt.trusted_score, attempt.trusted_at = float(score), now
    mark(db, attempt_id=attempt.id, campaign_id=campaign.id)
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:attempt:{attempt.id}:trusted:{now.timestamp()}",
        type="trusted_score_recorded",
        details={"attempt_id": attempt.id, "cycle_no": attempt.cycle_no, "score": float(score)},
        actor=actor, clock=clock,
    )
    if (
        campaign.mode == "target" and attempt.valid is not False
        and reaches_target(float(score), campaign.metric)
    ):
        try:
            transition(campaign.status, "trusted_target_reached")
        except IllegalTransition:
            return False
        await _transition(
            db, campaign, "trusted_target_reached", actor=actor, clock=clock,
            details={"attempt_id": attempt.id, "trusted_score": float(score)},
            stop_reason="achieved", final_attempt_id=attempt.id,
        )
        return True
    return False


__all__ = [
    "CampaignConflict",
    "confirm_campaign",
    "edit_campaign",
    "open_cycle_no",
    "pause_campaign",
    "record_trusted_score",
    "reject_campaign",
    "resume_campaign",
    "select_final",
]
