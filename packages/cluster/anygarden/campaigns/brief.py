"""Cycle brief (#930 §3.4): the spec of each cycle Task.

A pure function of the campaign and its attempts, so a restart renders
the same brief and tests need no DB. The brief is capped at
``BRIEF_MAX_CHARS``: only the best attempt, the last five cycles, outcome
tallies and the hypotheses of older cycles without gain go in; the rest of
the history is for ``read_campaign_attempts``.
Free text is clipped per field, and the rules section is always kept
whole at the end.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

from anygarden.campaigns.policy import BRIEF_MAX_CHARS, PLATEAU_WINDOW, usable_tokens
from anygarden.db.models import Campaign, CampaignAttempt

SECTION_TITLES: tuple[str, ...] = (
    "## Purpose",
    "## Criteria and status",
    "## Best attempt so far",
    "## Recent attempts",
    "## Failure patterns",
    "## Directives for this cycle",
    "## Rules",
)

STRATEGY_CHANGE_REASON = "change_strategy"
"""``Campaign.wake_reason`` set by the judge when an optimize campaign
plateaus (mirrors ``lifecycle.STRATEGY_CHANGE``)."""

RECENT_ATTEMPTS = 5
RECENT_LESSONS = 5
OLDER_FAILED_MAX = 30
"""Older cycles without gain listed under failure patterns, newest first."""
DEADLINE_CLOSE_RATIO = 0.10

_PURPOSE_MAX = 3000
_FIELD_MAX = 500
_LESSON_MAX = 300
_OLDER_FAILED_FIELD_MAX = 160
_NO_GAIN_OUTCOMES = frozenset({"no_gain", "regressed", "guardrail_violation", "invalid"})
"""Outcomes that judge the idea itself; evaluation and record failures do not."""
_TRUNCATED = "…"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(_TRUNCATED))] + _TRUNCATED


def _score(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"


def _pct(part: float, whole: float) -> str:
    if whole <= 0:
        return "0%"
    return f"{max(0.0, min(1.0, part / whole)) * 100:.0f}%"


def _time_left(campaign: Campaign, now: datetime) -> tuple[str, float | None]:
    if campaign.deadline_at is None:
        return "no deadline", None
    deadline = _aware(campaign.deadline_at)
    start = _aware(campaign.created_at) if campaign.created_at else now
    window = (deadline - start).total_seconds()
    left = (deadline - now).total_seconds()
    ratio = max(0.0, left / window) if window > 0 else 0.0
    hours = max(0.0, left / 3600)
    return f"{hours:.1f}h left ({_pct(left, window)} of the window), deadline {deadline.isoformat()}", ratio


def _criteria(campaign: Campaign, cycle_no: int, now: datetime) -> str:
    metric = campaign.metric or {}
    usable = usable_tokens(campaign.budget)
    left = max(0, usable - int(campaign.tokens_used or 0))
    time_text, _ = _time_left(campaign, now)
    target = metric.get("target")
    lines = [
        f"- Cycle {cycle_no} · mode `{campaign.mode}`",
        f"- Metric `{metric.get('name')}` ({metric.get('direction')})"
        + (f", target {_score(target)}" if target is not None else ", no target — best result by the deadline")
        + (f", minimum improvement {_score(metric.get('min_delta'))}" if metric.get("min_delta") else ""),
        f"- Budget: {left} of {usable} cycle tokens left ({_pct(left, usable)})",
        f"- Time: {time_text}",
    ]
    return "\n".join(lines)


def _best(campaign: Campaign, attempts: Sequence[CampaignAttempt]) -> str:
    best = next((a for a in attempts if a.id == campaign.best_attempt_id), None)
    if best is None:
        return "No scored attempt yet."
    lines = [
        f"- Cycle {best.cycle_no}: score {_score(best.dev_score)}"
        + (f", trusted score {_score(best.trusted_score)}" if best.trusted_score is not None else ""),
        f"- Hypothesis: {_clip(best.hypothesis, _FIELD_MAX) or '—'}",
        f"- Change: {_clip(best.change_summary, _FIELD_MAX) or '—'}",
    ]
    if best.artifact_ref:
        lines.append(f"- Artifact: {_clip(best.artifact_ref, _FIELD_MAX)}")
    return "\n".join(lines)


def _recent(cycles: Sequence[CampaignAttempt]) -> str:
    recent = [a for a in cycles if a.closed_at is not None][-RECENT_ATTEMPTS:]
    if not recent:
        return "None yet."
    rows = []
    for a in recent:
        rows.append(
            f"- Cycle {a.cycle_no}: {_clip(a.hypothesis, _FIELD_MAX) or '(no hypothesis recorded)'}"
            f" → score {_score(a.dev_score)} → {a.outcome or 'unknown'}"
            + (f" → lesson: {_clip(a.lessons, _LESSON_MAX)}" if a.lessons else "")
        )
    return "\n".join(rows)


def _patterns(cycles: Sequence[CampaignAttempt]) -> str:
    closed = [a for a in cycles if a.closed_at is not None]
    if not closed:
        return "None yet."
    tally = Counter(a.outcome or "unknown" for a in closed)
    lines = ["Outcomes: " + ", ".join(f"{k}: {v}" for k, v in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))]
    lessons = [a for a in closed if a.lessons][-RECENT_LESSONS:]
    if lessons:
        lines.append("Recent lessons:")
        lines.extend(f"- Cycle {a.cycle_no}: {_clip(a.lessons, _LESSON_MAX)}" for a in lessons)
    # Ideas the evaluator already judged, from cycles that left "Recent attempts".
    older = [a for a in closed[:-RECENT_ATTEMPTS] if a.outcome in _NO_GAIN_OUTCOMES and a.hypothesis]
    if older:
        lines.append(
            "Tried earlier without gain (scored by the evaluator on the code of that cycle)."
            " Do not repeat one unchanged. A retry is fine when the code or setup has changed"
            " since then: say in your hypothesis what changed and why it may help now:"
        )
        lines.extend(
            f"- Cycle {a.cycle_no} ({a.outcome}): {_clip(a.hypothesis, _OLDER_FAILED_FIELD_MAX)}"
            for a in reversed(older[-OLDER_FAILED_MAX:])
        )
        if len(older) > OLDER_FAILED_MAX:
            lines.append(
                f"- …and {len(older) - OLDER_FAILED_MAX} older ones (read_campaign_attempts)."
            )
    return "\n".join(lines)


def _directives(campaign: Campaign, cycles: Sequence[CampaignAttempt], now: datetime) -> str:
    items: list[str] = []
    if campaign.wake_reason == STRATEGY_CHANGE_REASON:
        items.append(
            f"- **Change strategy.** {PLATEAU_WINDOW} cycles in a row brought no improvement:"
            " try a substantially different approach instead of tuning the current one."
        )
    elif campaign.mode == "optimize" and (campaign.cycles_since_improvement or 0) >= PLATEAU_WINDOW:
        items.append(
            f"- **Change strategy.** {campaign.cycles_since_improvement} cycles without improvement:"
            " try a substantially different approach instead of tuning the current one."
        )
    closed = [a for a in cycles if a.closed_at is not None]
    if closed and closed[-1].wake_reason:
        items.append(
            f"- You scheduled this wake-up: {_clip(closed[-1].wake_reason, _FIELD_MAX)}"
        )
    if closed and closed[-1].recorded_at is None:
        items.append(
            "- **record_campaign_attempt was not called** in the previous cycle. Record this"
            " cycle's attempt before you finish."
        )
    _, ratio = _time_left(campaign, now)
    if ratio is not None and ratio < DEADLINE_CLOSE_RATIO:
        items.append(
            "- **Deadline is close.** Prefer a small, safe change that keeps the best result"
            " valid over a risky rewrite."
        )
    return "\n".join(items) if items else "None."


def _rules(campaign: Campaign) -> str:
    pins = (campaign.evaluator or {}).get("pin_paths") or []
    pin_text = ", ".join(f"`{p}`" for p in pins[:20]) if pins else "none"
    return "\n".join([
        "1. Call `begin_project_execution` as your first action; this task is its root.",
        (
            "2. Do the work yourself (delegating is optional). Before finishing, call"
            " `record_campaign_attempt` exactly once with what you tried, then read the"
            " follow-up instructions in its response."
        ),
        (
            "3. Finish with `seal_project_plan` — `required_task_ids=[]` when you delegated"
            " nothing — then `complete_project_execution`."
        ),
        (
            "4. The system runs the evaluator and decides the score. Do not modify the"
            f" evaluator command or the pinned files ({pin_text})."
        ),
        (
            "5. `schedule_campaign_wakeup` sets when the next cycle may start after this one"
            " ends (e.g. an external leaderboard or a long job). It is not a way to wait"
            " inside this cycle: for your own child task, end the turn and the server"
            " resumes you."
        ),
        (
            "6. If you cannot make progress without a person, call `report_campaign_blocked`"
            " with what would unblock you; older attempts are in `read_campaign_attempts`."
        ),
        (
            "7. Save what this cycle produced for people (a list, table, document, chart) as files"
            f" in `memory/outbox/{campaign.room_id}/`; they are attached to the goal's reports."
        ),
    ])


def render_brief(
    campaign: Campaign,
    attempts: Sequence[CampaignAttempt],
    *,
    cycle_no: int,
    now: datetime,
) -> str:
    """Render the brief for cycle *cycle_no* (≤ ``BRIEF_MAX_CHARS``).

    *attempts* are the campaign's attempts in any order; retrospective
    attempts are excluded from the cycle history.
    """
    now = _aware(now)
    cycles = sorted((a for a in attempts if a.kind != "retrospective"), key=lambda a: a.cycle_no)
    header = f"# Campaign cycle {cycle_no}: {_clip(campaign.title, 200)}"
    bodies = [
        _clip(campaign.purpose, _PURPOSE_MAX),
        _criteria(campaign, cycle_no, now),
        _best(campaign, attempts),
        _recent(cycles),
        _patterns(cycles),
        _directives(campaign, cycles, now),
    ]
    rules = f"{SECTION_TITLES[6]}\n{_rules(campaign)}"
    # Fit the first six sections into what the rules leave, clipping each
    # proportionally so every heading (and its order) survives.
    budget = BRIEF_MAX_CHARS - len(header) - len(rules) - 2 * (len(bodies) + 2)
    titled = [f"{title}\n" for title in SECTION_TITLES[:6]]
    body_budget = budget - sum(len(t) for t in titled)
    total = sum(len(b) for b in bodies)
    if total > body_budget:
        share = max(0, body_budget // len(bodies))
        bodies = [_clip(b, share) for b in bodies]
    sections = [header, *(t + b for t, b in zip(titled, bodies, strict=True)), rules]
    text = "\n\n".join(sections)
    if len(text) > BRIEF_MAX_CHARS:  # pragma: no cover — defensive; the math above fits
        text = text[: BRIEF_MAX_CHARS - len(rules) - 2] + "\n\n" + rules
    return text


# ── Retrospective (Phase 6, §3.6) ──────────────────────────────────

RETRO_SECTION_TITLES: tuple[str, ...] = (
    "## Purpose",
    "## Why this retrospective",
    "## Statistics",
    "## Changes that helped",
    "## Changes that did not help",
    "## Recorded lessons",
    "## Current instructions",
    "## Rules",
)

_RETRO_REASONS = {
    "periodic": "A regular checkpoint: the campaign has run another block of cycles.",
    "achieved": "The campaign reached its target.",
    "deadline_reached": "The campaign's deadline passed.",
    "budget_exhausted": "The campaign used up its cycle budget.",
}
_RETRO_LIST = 10
_AGENTS_MD_MAX = 4000


def _retro_changes(cycles: Sequence[CampaignAttempt], outcomes: set[str]) -> str:
    rows = [a for a in cycles if a.outcome in outcomes][-_RETRO_LIST:]
    if not rows:
        return "None."
    return "\n".join(
        f"- Cycle {a.cycle_no} ({a.outcome}, score {_score(a.dev_score)}):"
        f" {_clip(a.hypothesis, _FIELD_MAX) or '(no hypothesis)'}"
        f" — change: {_clip(a.change_summary, _FIELD_MAX) or '—'}"
        for a in rows
    )


def _retro_rules() -> str:
    from anygarden.campaigns.retrospective import (
        LEARNINGS_HEADING,
        MAX_CHANGE_BYTES,
        MAX_CHANGES_PER_RETROSPECTIVE,
    )

    return "\n".join([
        "1. Call `begin_project_execution` as your first action; this task is its root.",
        (
            "2. Keep only lessons that will help in future work, not a diary of this campaign."
            " Generalise; drop what turned out wrong."
        ),
        (
            f"3. Use `apply_campaign_learning` at most {MAX_CHANGES_PER_RETROSPECTIVE} times"
            f" (≤ {MAX_CHANGE_BYTES // 1024}KB each). `target=agents_md` replaces the body of"
            f" the `{LEARNINGS_HEADING}` section of your AGENTS.md (nothing else is changed);"
            " `target=skill` creates a new skill or updates one this campaign created."
        ),
        (
            "4. Changes apply without approval. Each is recorded as an instructions revision"
            " people can restore, and announced in the room."
        ),
        (
            "5. Do not call `record_campaign_attempt` or run the evaluator. Finish with"
            " `seal_project_plan` (`required_task_ids=[]`), then `complete_project_execution`."
        ),
    ])


def render_retro_brief(
    campaign: Campaign,
    attempts: Sequence[CampaignAttempt],
    *,
    reason: str,
    cycle_no: int,
    now: datetime,
    agents_md: str,
    skills: Sequence[str],
    created_skills: set[str] | frozenset[str] = frozenset(),
) -> str:
    """Render the brief for a retrospective cycle (≤ ``BRIEF_MAX_CHARS``)."""
    now = _aware(now)
    cycles = sorted((a for a in attempts if a.kind != "retrospective"), key=lambda a: a.cycle_no)
    closed = [a for a in cycles if a.closed_at is not None]
    tally = Counter(a.outcome or "unknown" for a in closed)
    usable = usable_tokens(campaign.budget)
    stats = [
        f"- Cycles run: {campaign.cycles_run}; status `{campaign.status}`",
        "- Outcomes: " + (", ".join(f"{k}: {v}" for k, v in sorted(tally.items())) or "none"),
        f"- Tokens used: {int(campaign.tokens_used or 0)} of {usable} cycle tokens",
        _criteria(campaign, cycle_no, now),
        "Best attempt:",
        _best(campaign, attempts),
    ]
    lessons = [a for a in closed if a.lessons][-_RETRO_LIST:]
    skill_lines = [
        f"- `{path}`" + (" (created by this campaign)" if path in created_skills else "")
        for path in skills
    ]
    instructions = (
        "AGENTS.md:\n```markdown\n" + (_clip(agents_md, _AGENTS_MD_MAX) or "(empty)") + "\n```\n"
        "Skills:\n" + ("\n".join(skill_lines) if skill_lines else "(none)")
    )
    header = f"# Campaign retrospective (cycle {cycle_no}): {_clip(campaign.title, 200)}"
    bodies = [
        _clip(campaign.purpose, _PURPOSE_MAX),
        _RETRO_REASONS.get(reason, reason),
        "\n".join(stats),
        _retro_changes(closed, {"improved"}),
        _retro_changes(closed, {"regressed", "no_gain", "guardrail_violation"}),
        "\n".join(f"- Cycle {a.cycle_no}: {_clip(a.lessons, _LESSON_MAX)}" for a in lessons)
        or "None.",
        instructions,
    ]
    rules = f"{RETRO_SECTION_TITLES[-1]}\n{_retro_rules()}"
    titled = [f"{title}\n" for title in RETRO_SECTION_TITLES[:-1]]
    budget = BRIEF_MAX_CHARS - len(header) - len(rules) - 2 * (len(bodies) + 2)
    body_budget = budget - sum(len(t) for t in titled)
    if sum(len(b) for b in bodies) > body_budget:
        share = max(0, body_budget // len(bodies))
        bodies = [_clip(b, share) for b in bodies]
    return "\n\n".join([header, *(t + b for t, b in zip(titled, bodies, strict=True)), rules])


__all__ = ["RETRO_SECTION_TITLES", "SECTION_TITLES", "render_brief", "render_retro_brief"]
