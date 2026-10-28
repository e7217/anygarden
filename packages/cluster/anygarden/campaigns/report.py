"""A goal reports its result in the room's main chat (#964).

The goal loop runs in cycle Tasks whose messages live in threads, and a
status change (#957) happens on the server with no agent turn to explain
it. So at the moments a person needs to read the result the server opens
a **report turn** for the goal's lead:

| reason          | when                                                        |
|-----------------|-------------------------------------------------------------|
| ``confirmation``| the goal reached its target and waits for confirmation      |
| ``ended``       | achieved (not by the person's own confirm), deadline, budget|
| ``needs_person``| blocked or plateau                                          |
| ``progress``    | every ``RETRO_EVERY_CYCLES`` closed cycles while still active|

:func:`request_report` posts one top-level system message that mentions
the lead, keeps the ``goal_status`` block (the chat renders it as the goal
status card, so the body — the agent's brief — is not shown to people,
like a ``/goal`` draft request) and carries the cycle's deliverables as
``artifact`` references. Then it opens the lead's turn on that message
(the ``workspace/drafts.py`` pattern), so the reply lands in the main chat.

Deliverables are :class:`RoomArtifact` rows the lead produced in the
goal's room during a cycle (:func:`cycle_artifacts`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Campaign, CampaignAttempt, Message, RoomArtifact

SYSTEM_ORIGIN = "goal_report"
MAX_REPORT_ARTIFACTS = 10
"""At most this many files ride on a report request."""
_PER_CYCLE = MAX_REPORT_ARTIFACTS // 2
_FIELD_MAX = 300

REPORT_REASONS: frozenset[str] = frozenset({"confirmation", "ended", "needs_person", "progress"})

_STATUS_REASON = {
    "awaiting_confirmation": "confirmation",
    "achieved": "ended",
    "deadline_reached": "ended",
    "budget_exhausted": "ended",
    "blocked": "needs_person",
    "plateau": "needs_person",
}


def report_reason(new_status: str, *, event: str) -> str | None:
    """The report a transition into *new_status* asks for, if any.

    ``stopped`` is never reported (a person stopped it, or the room was
    archived), and neither is ``achieved`` by the person's own confirm:
    the lead already reported when the goal began waiting for it.
    """
    if new_status == "achieved" and event == "confirm":
        return None
    return _STATUS_REASON.get(new_status)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _clip(text: str | None, limit: int = _FIELD_MAX) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _score(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"


def artifact_reference(row: RoomArtifact) -> dict[str, Any]:
    """The canonical ``artifact`` reference (``messages/references.py``)."""
    return {
        "type": "artifact",
        "id": row.id,
        "room_id": row.room_id,
        "name": row.filename,
        "mime": row.mime,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
    }


async def cycle_artifacts(
    db: AsyncSession, campaign: Campaign, attempt: CampaignAttempt
) -> list[RoomArtifact]:
    """Files the lead produced in the goal's room during *attempt*.

    The window runs from the cycle's opening to the next attempt's opening
    (or now): a turn's outbox files reach the server when its reply is
    sent, which is after the run finished (``closed_at``). Cycles never
    overlap, so the next opening is the cycle's real end.
    """
    if campaign.agent_id is None:
        return []
    start = _aware(attempt.opened_at)
    following = await db.scalar(
        select(CampaignAttempt.opened_at)
        .where(
            CampaignAttempt.campaign_id == campaign.id,
            CampaignAttempt.opened_at > start,
            CampaignAttempt.id != attempt.id,
        )
        .order_by(CampaignAttempt.opened_at)
        .limit(1)
    )
    query = select(RoomArtifact).where(
        RoomArtifact.room_id == campaign.room_id,
        RoomArtifact.produced_by_agent_id == campaign.agent_id,
        RoomArtifact.created_at >= start,
    )
    if following is not None:
        query = query.where(RoomArtifact.created_at < _aware(following))
    rows = await db.scalars(query.order_by(RoomArtifact.created_at, RoomArtifact.id))
    return list(rows)


async def _report_cycles(
    db: AsyncSession, campaign: Campaign
) -> tuple[CampaignAttempt | None, CampaignAttempt | None]:
    """The best scored cycle and the latest closed work cycle."""
    best = await db.get(CampaignAttempt, campaign.best_attempt_id) if campaign.best_attempt_id else None
    latest = await db.scalar(
        select(CampaignAttempt)
        .where(
            CampaignAttempt.campaign_id == campaign.id,
            CampaignAttempt.kind != "retrospective",
            CampaignAttempt.closed_at.is_not(None),
        )
        .order_by(CampaignAttempt.cycle_no.desc())
        .limit(1)
    )
    return best, latest


async def report_artifacts(db: AsyncSession, campaign: Campaign) -> list[RoomArtifact]:
    """Files of the best and the latest cycle, without duplicates, at most
    :data:`MAX_REPORT_ARTIFACTS` (half each when both have many)."""
    best, latest = await _report_cycles(db, campaign)
    cycles = [a for a in (best, latest) if a is not None]
    if len(cycles) == 2 and cycles[0].id == cycles[1].id:
        cycles = cycles[:1]
    groups = [await cycle_artifacts(db, campaign, attempt) for attempt in cycles]
    seen: set[str] = set()
    picked: list[RoomArtifact] = []

    def take(rows: Sequence[RoomArtifact], limit: int) -> None:
        for row in rows:
            if len(picked) >= limit:
                return
            if row.id not in seen:
                seen.add(row.id)
                picked.append(row)

    share = _PER_CYCLE if len(groups) > 1 else MAX_REPORT_ARTIFACTS
    for rows in groups:
        take(rows, len(picked) + share)
    for rows in groups:  # fill what one cycle left unused
        take(rows, MAX_REPORT_ARTIFACTS)
    return picked


_STATUS_LINE = {
    "confirmation": "reached its target and waits for a person to confirm it",
    "ended": "has ended",
    "needs_person": "needs a person",
    "progress": "is still running",
}

_NEXT_ACTION = {
    "confirmation": (
        "What the person should check before they confirm or reject the result on the goal,"
        " and what you would change if they reject it."
    ),
    "ended": (
        "What is still limited or unverified in the result, and what the person could do next"
        " (use it, extend the goal, or start a new one)."
    ),
    "needs_person": (
        "Why the goal stopped, and exactly what the person needs to do so it can continue."
    ),
    "progress": (
        "The score trend and your plan for the next cycles. The goal keeps running; keep the"
        " reply to about five lines plus the deliverable summary."
    ),
}


def _cycle_line(label: str, attempt: CampaignAttempt) -> str:
    score = _score(attempt.dev_score)
    if attempt.trusted_score is not None:
        score += f" (trusted {_score(attempt.trusted_score)})"
    parts = [f"- {label}: cycle {attempt.cycle_no}, score {score}, outcome {attempt.outcome or '—'}"]
    if attempt.hypothesis:
        parts.append(f"  - Hypothesis: {_clip(attempt.hypothesis)}")
    if attempt.change_summary:
        parts.append(f"  - Change: {_clip(attempt.change_summary)}")
    if attempt.artifact_ref:
        parts.append(f"  - Artifact: {_clip(attempt.artifact_ref)}")
    return "\n".join(parts)


def render_report_request(
    campaign: Campaign,
    *,
    reason: str,
    lead_participant_id: str | None,
    best: CampaignAttempt | None,
    latest: CampaignAttempt | None,
    files: Sequence[RoomArtifact],
    detail: str | None = None,
) -> str:
    """The report request's body: a status line, then the lead's brief."""
    metric = campaign.metric or {}
    target = metric.get("target")
    best_score = _score(best.dev_score) if best is not None else "—"
    if best is not None and best.trusted_score is not None:
        best_score += f" (trusted {_score(best.trusted_score)})"
    if reason == "progress":
        head = f"Goal “{campaign.title}” has run {campaign.cycles_run} cycles and is still running."
    else:
        head = f"Goal “{campaign.title}” {_STATUS_LINE[reason]}."
    if detail:
        head = f"{head} {detail}"
    mention = f"<@user:{lead_participant_id}> " if lead_participant_id else ""
    target_text = (
        f"target {_score(target)} ({metric.get('direction', 'maximize')} {metric.get('name', 'score')})"
        if target is not None else f"no target; {metric.get('direction', 'maximize')} {metric.get('name', 'score')}"
    )
    lines = [
        f"{mention}Goal report request — {head}",
        "",
        (
            "Write the result report for the people in this room as your reply. They have not"
            " seen the cycle threads, so the reply must stand on its own. It must contain:"
        ),
        (
            f"1. One line with the result: {target_text} vs best score {best_score}, status"
            f" `{campaign.status}`."
        ),
        (
            "2. The actual content of the deliverable, summarized in the reply itself — e.g. the"
            " top items of a list or table and the total count, or the key sections of a"
            " document. Read the files in your workspace; never answer only \"see the file\"."
        ),
        "3. One to three attempts that worked and why.",
        f"4. {_NEXT_ACTION[reason]}",
        (
            "5. Attach any further supporting file by writing it to"
            f" `memory/outbox/{campaign.room_id}/`; the files listed below are already attached"
            " to this request."
        ),
        (
            "Reply in the language of this room's conversation. Do not mention any other agent."
            " Do not start a cycle or change the goal in this turn."
        ),
        "",
        "Context:",
        f"- Purpose: {_clip(campaign.purpose, 500)}",
        f"- Cycles run: {campaign.cycles_run}",
    ]
    if best is not None:
        lines.append(_cycle_line("Best", best))
    if latest is not None and (best is None or latest.id != best.id):
        lines.append(_cycle_line("Latest", latest))
    if files:
        lines.append("- Attached files: " + ", ".join(f"`{f.filename}`" for f in files))
    else:
        lines.append("- Attached files: none were found for these cycles.")
    return "\n".join(lines)


def report_key(campaign: Campaign, reason: str) -> str:
    return f"goal-report:{campaign.id}:{reason}:{campaign.state_revision}"


async def request_report(
    db: AsyncSession,
    campaign: Campaign,
    *,
    reason: str,
    previous: str | None = None,
    detail: str | None = None,
) -> Message | None:
    """Post the report request for *campaign* and open its lead's turn.

    Returns the message, or ``None`` when this state was already reported
    (the lead's turn for the same campaign, reason and ``state_revision``
    exists) or the room takes no direct messages. A lead that is not in the room gets no mention and no
    turn; the message (status card and files) still stands.
    """
    from anygarden.campaigns.notify import mark
    from anygarden.campaigns.service import find_agent_participant
    from anygarden.db.models import AgentTurn
    from anygarden.messages.service import append_message
    from anygarden.shared_channels.schemas import ChannelError
    from anygarden.turns.service import create_turn

    if reason not in REPORT_REASONS:
        raise ValueError(f"unknown report reason {reason!r}")
    key = report_key(campaign, reason)
    if await db.scalar(select(AgentTurn.request_id).where(AgentTurn.idempotency_key == key)):
        return None
    participant = (
        await find_agent_participant(db, room_id=campaign.room_id, agent_id=campaign.agent_id)
        if campaign.agent_id else None
    )
    pid = participant.id if participant is not None else None
    best, latest = await _report_cycles(db, campaign)
    files = await report_artifacts(db, campaign)
    detail = detail if isinstance(detail, str) and detail else None
    content = render_report_request(
        campaign, reason=reason, lead_participant_id=pid, best=best, latest=latest,
        files=files, detail=detail,
    )
    metadata: dict[str, Any] = {
        "goal_status": {
            "campaign_id": campaign.id, "title": campaign.title, "status": campaign.status,
            "previous": previous, "detail": detail,
        },
        "system_origin": SYSTEM_ORIGIN,
        SYSTEM_ORIGIN: {
            "campaign_id": campaign.id, "reason": reason, "key": key,
            "lead_participant_id": pid,
        },
    }
    if pid is not None:
        metadata["mentions"] = [{"type": "user", "id": pid}]
    if files:
        metadata["references"] = [artifact_reference(f) for f in files]
    try:
        message = await append_message(db, campaign.room_id, None, content, metadata)
    except ChannelError:
        return None
    mark(db, message_id=message.id)
    if participant is not None and campaign.agent_id is not None:
        await create_turn(
            db, room_id=campaign.room_id, participant_id=participant.id,
            agent_id=campaign.agent_id, trigger_message_id=message.id,
            thread_root_id=message.root_message_id, idempotency_key=key,
        )
    return message


__all__ = [
    "MAX_REPORT_ARTIFACTS",
    "REPORT_REASONS",
    "SYSTEM_ORIGIN",
    "artifact_reference",
    "cycle_artifacts",
    "render_report_request",
    "report_artifacts",
    "report_key",
    "report_reason",
    "request_report",
]
