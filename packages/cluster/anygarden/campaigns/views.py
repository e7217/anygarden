"""JSON views of campaigns and attempts (#930 Phase 7).

One shape for the REST responses, the ``campaign.updated`` /
``campaign.attempt_updated`` WS frames and the agent's
``read_campaign_attempts`` tool, so the UI can merge a pushed frame into
what it fetched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.campaigns.policy import usable_tokens
from anygarden.db.models import Campaign, CampaignAttempt


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def attempt_view(attempt: CampaignAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "campaign_id": attempt.campaign_id,
        "cycle_no": attempt.cycle_no,
        "kind": attempt.kind,
        "task_id": attempt.task_id,
        "execution_id": attempt.execution_id,
        "opened_at": _iso(attempt.opened_at),
        "closed_at": _iso(attempt.closed_at),
        "agent_outcome": attempt.agent_outcome,
        "hypothesis": attempt.hypothesis,
        "change_summary": attempt.change_summary,
        "artifact_ref": attempt.artifact_ref,
        "lessons": attempt.lessons,
        "recorded_at": _iso(attempt.recorded_at),
        "wake_at": _iso(attempt.wake_at),
        "wake_reason": attempt.wake_reason,
        "block_reason": attempt.block_reason,
        "unblock_hint": attempt.unblock_hint,
        "eval_status": attempt.eval_status,
        "dev_score": attempt.dev_score,
        "metrics": attempt.metrics,
        "valid": attempt.valid,
        "eval_excerpt": attempt.eval_excerpt,
        "eval_started_at": _iso(attempt.eval_started_at),
        "eval_finished_at": _iso(attempt.eval_finished_at),
        "trusted_score": attempt.trusted_score,
        "trusted_at": _iso(attempt.trusted_at),
        "tokens": attempt.tokens,
        "outcome": attempt.outcome,
    }


def _score_ref(attempt: CampaignAttempt | None) -> dict[str, Any] | None:
    if attempt is None:
        return None
    return {
        "attempt_id": attempt.id,
        "cycle_no": attempt.cycle_no,
        "dev_score": attempt.dev_score,
        "trusted_score": attempt.trusted_score,
        "verified": attempt.trusted_score is not None,
    }


async def campaign_view(db: AsyncSession, campaign: Campaign) -> dict[str, Any]:
    best = (
        await db.get(CampaignAttempt, campaign.best_attempt_id)
        if campaign.best_attempt_id else None
    )
    final = (
        await db.get(CampaignAttempt, campaign.final_attempt_id)
        if campaign.final_attempt_id else None
    )
    open_attempt = await db.scalar(
        select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == campaign.id,
            CampaignAttempt.closed_at.is_(None),
        ).limit(1)
    )
    return {
        "id": campaign.id,
        "room_id": campaign.room_id,
        "owner_id": campaign.owner_id,
        "agent_id": campaign.agent_id,
        "title": campaign.title,
        "purpose": campaign.purpose,
        "mode": campaign.mode,
        "metric": campaign.metric,
        "evaluator": campaign.evaluator,
        "trusted_eval": campaign.trusted_eval,
        "budget": campaign.budget,
        "usable_tokens": usable_tokens(campaign.budget),
        "deadline_at": _iso(campaign.deadline_at),
        "status": campaign.status,
        "stop_reason": campaign.stop_reason,
        "status_detail": campaign.status_detail,
        "cycles_run": campaign.cycles_run,
        "tokens_used": int(campaign.tokens_used or 0),
        "best": _score_ref(best),
        "final": _score_ref(final),
        "open_attempt": (
            {"attempt_id": open_attempt.id, "cycle_no": open_attempt.cycle_no,
             "kind": open_attempt.kind, "eval_status": open_attempt.eval_status,
             "task_id": open_attempt.task_id}
            if open_attempt is not None else None
        ),
        "next_cycle_at": _iso(campaign.next_cycle_at),
        "cycles_since_improvement": campaign.cycles_since_improvement,
        "consecutive_eval_failures": campaign.consecutive_eval_failures,
        "retro_due": campaign.retro_due,
        "state_revision": campaign.state_revision,
        "created_at": _iso(campaign.created_at),
        "updated_at": _iso(campaign.updated_at),
    }


__all__ = ["attempt_view", "campaign_view"]
