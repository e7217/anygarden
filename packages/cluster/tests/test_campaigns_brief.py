"""Cycle brief rendering (#930 §3.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anygarden.campaigns.brief import SECTION_TITLES, render_brief
from anygarden.campaigns.policy import BRIEF_MAX_CHARS, PLATEAU_WINDOW
from anygarden.db.models import Campaign, CampaignAttempt

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _campaign(**overrides) -> Campaign:
    values = {
        "id": "camp-1",
        "room_id": "room-1",
        "title": "Score",
        "purpose": "Ship a solver that wins the contest.",
        "mode": "target",
        "metric": {"name": "accuracy", "direction": "maximize", "target": 0.9, "min_delta": 0.0},
        "evaluator": {"command": "python bench.py", "cwd": ".", "timeout_sec": 600,
                      "pin_paths": ["bench.py"], "score_key": "score"},
        "trusted_eval": "none",
        "budget": {"max_total_tokens": 1000, "retro_reserve_ratio": 0.0},
        "deadline_at": None,
        "status": "active",
        "cycles_run": 0,
        "tokens_used": 250,
        "cycles_since_improvement": 0,
        "best_attempt_id": None,
        "created_at": NOW - timedelta(days=1),
    }
    values.update(overrides)
    return Campaign(**values)


def _attempt(n: int, **overrides) -> CampaignAttempt:
    values = {
        "id": f"att-{n}",
        "campaign_id": "camp-1",
        "cycle_no": n,
        "kind": "cycle",
        "opened_at": NOW - timedelta(hours=10 - n),
        "closed_at": NOW - timedelta(hours=10 - n) + timedelta(minutes=30),
        "hypothesis": f"hypothesis {n}",
        "change_summary": f"change {n}",
        "lessons": f"lesson {n}",
        "recorded_at": NOW - timedelta(hours=10 - n) + timedelta(minutes=20),
        "dev_score": 0.5 + n / 100,
        "outcome": "no_gain",
        "eval_status": "ok",
    }
    values.update(overrides)
    return CampaignAttempt(**values)


def _positions(text: str) -> list[int]:
    return [text.index(title) for title in SECTION_TITLES]


def test_sections_appear_in_order():
    brief = render_brief(_campaign(), [], cycle_no=1, now=NOW)
    positions = _positions(brief)
    assert positions == sorted(positions)
    assert len(SECTION_TITLES) == 7
    assert "Ship a solver that wins the contest." in brief
    assert "Cycle 1" in brief


def test_criteria_show_metric_target_and_remaining_budget():
    brief = render_brief(_campaign(), [], cycle_no=1, now=NOW)
    assert "accuracy" in brief and "maximize" in brief and "0.9" in brief
    assert "75%" in brief  # 750 of 1000 usable tokens left


def test_remaining_time_is_shown_with_a_deadline():
    campaign = _campaign(deadline_at=NOW + timedelta(days=1))
    brief = render_brief(campaign, [], cycle_no=1, now=NOW)
    assert "50%" in brief  # half of the two-day window is left


def test_best_attempt_and_last_five():
    attempts = [_attempt(n) for n in range(1, 9)]
    attempts[2].dev_score = 0.88
    attempts[2].outcome = "improved"
    attempts[2].artifact_ref = "git:abc123"
    attempts[2].trusted_score = 0.86
    brief = render_brief(_campaign(best_attempt_id="att-3"), attempts, cycle_no=9, now=NOW)
    best = brief[brief.index(SECTION_TITLES[2]):brief.index(SECTION_TITLES[3])]
    assert "0.88" in best and "hypothesis 3" in best and "git:abc123" in best and "0.86" in best
    recent = brief[brief.index(SECTION_TITLES[3]):brief.index(SECTION_TITLES[4])]
    for n in range(4, 9):
        assert f"hypothesis {n}" in recent
    for n in range(1, 4):
        assert f"hypothesis {n}" not in recent


def test_failure_patterns_tally_every_outcome():
    attempts = [_attempt(1, outcome="improved"), _attempt(2, outcome="regressed"),
                _attempt(3, outcome="regressed"), _attempt(4, outcome="eval_failed")]
    brief = render_brief(_campaign(), attempts, cycle_no=5, now=NOW)
    patterns = brief[brief.index(SECTION_TITLES[4]):brief.index(SECTION_TITLES[5])]
    assert "regressed: 2" in patterns
    assert "improved: 1" in patterns
    assert "eval_failed: 1" in patterns


def test_no_directives_when_nothing_is_unusual():
    brief = render_brief(_campaign(), [_attempt(1)], cycle_no=2, now=NOW)
    directives = brief[brief.index(SECTION_TITLES[5]):brief.index(SECTION_TITLES[6])]
    assert "None." in directives


@pytest.mark.req("CMP-08")
def test_optimize_plateau_asks_for_a_strategy_change():
    campaign = _campaign(mode="optimize", metric={"name": "loss", "direction": "minimize",
                                                  "target": None, "min_delta": 0.0},
                         deadline_at=NOW + timedelta(days=3),
                         cycles_since_improvement=PLATEAU_WINDOW)
    brief = render_brief(campaign, [], cycle_no=6, now=NOW)
    assert "Change strategy" in brief


def test_missing_record_is_warned():
    attempts = [_attempt(1, recorded_at=None, outcome="no_record")]
    brief = render_brief(_campaign(), attempts, cycle_no=2, now=NOW)
    assert "record_campaign_attempt was not called" in brief


def test_deadline_close_is_warned():
    campaign = _campaign(created_at=NOW - timedelta(days=10), deadline_at=NOW + timedelta(hours=12))
    brief = render_brief(campaign, [], cycle_no=3, now=NOW)
    assert "Deadline is close" in brief


def test_rules_name_the_tools():
    brief = render_brief(_campaign(), [], cycle_no=1, now=NOW)
    rules = brief[brief.index(SECTION_TITLES[6]):]
    for tool in ("begin_project_execution", "record_campaign_attempt", "schedule_campaign_wakeup"):
        assert tool in rules
    assert "bench.py" in rules  # pinned evaluator files must not be edited
    # The minimal sequence, in order: begin → record → seal (no children) → complete.
    order = [rules.index(t) for t in ("begin_project_execution", "record_campaign_attempt",
                                      "seal_project_plan", "complete_project_execution")]
    assert order == sorted(order)
    assert "required_task_ids=[]" in rules
    # A wake-up schedules the NEXT cycle; it is not a way to wait inside this one.
    assert "next cycle" in rules and "not a way to wait" in rules


@pytest.mark.req("CMP-18")
def test_rules_ask_for_deliverables_as_outbox_files():
    """#964 — the goal's reports attach the files the cycle left in the outbox."""
    brief = render_brief(_campaign(), [], cycle_no=1, now=NOW)
    rules = brief[brief.index(SECTION_TITLES[6]):]
    assert "memory/outbox/room-1/" in rules


def test_brief_is_capped_and_keeps_the_rules():
    huge = "x" * 50_000
    attempts = [_attempt(n, hypothesis=huge, change_summary=huge, lessons=huge) for n in range(1, 30)]
    campaign = _campaign(purpose=huge, best_attempt_id="att-2")
    brief = render_brief(campaign, attempts, cycle_no=30, now=NOW)
    assert len(brief) <= BRIEF_MAX_CHARS
    positions = _positions(brief)
    assert positions == sorted(positions)
    assert "record_campaign_attempt" in brief[brief.index(SECTION_TITLES[6]):]


def test_retrospective_attempts_are_not_counted_as_cycles():
    attempts = [_attempt(1), _attempt(2, kind="retrospective", hypothesis="retro", outcome=None)]
    brief = render_brief(_campaign(), attempts, cycle_no=3, now=NOW)
    recent = brief[brief.index(SECTION_TITLES[3]):brief.index(SECTION_TITLES[4])]
    assert "retro" not in recent


def _patterns_section(brief: str) -> str:
    return brief[brief.index(SECTION_TITLES[4]):brief.index(SECTION_TITLES[5])]


@pytest.mark.req("CMP-15")
def test_older_cycles_without_gain_stay_visible():
    # Cycles 1–3 fell out of "Recent attempts" but their hypotheses must not be lost.
    attempts = [_attempt(1, outcome="regressed", hypothesis="cache the parser"),
                _attempt(2, outcome="improved", hypothesis="vectorise the loop"),
                _attempt(3, outcome="no_gain", hypothesis="raise the batch size"),
                *(_attempt(n) for n in range(4, 9))]
    patterns = _patterns_section(render_brief(_campaign(), attempts, cycle_no=9, now=NOW))
    assert "cache the parser" in patterns and "regressed" in patterns
    assert "raise the batch size" in patterns
    # An improvement is not a failed idea, and recent cycles are already listed above.
    assert "vectorise the loop" not in patterns
    assert "hypothesis 4" not in patterns
    # Newest first.
    assert patterns.index("raise the batch size") < patterns.index("cache the parser")


@pytest.mark.req("CMP-15")
def test_evaluation_failures_are_not_listed_as_failed_ideas():
    attempts = [_attempt(1, outcome="eval_failed", hypothesis="idea with a broken eval"),
                _attempt(2, outcome="no_record", hypothesis="idea never recorded"),
                *(_attempt(n, outcome="improved") for n in range(3, 8))]
    patterns = _patterns_section(render_brief(_campaign(), attempts, cycle_no=8, now=NOW))
    assert "idea with a broken eval" not in patterns
    assert "idea never recorded" not in patterns
    assert "Tried earlier without gain" not in patterns


@pytest.mark.req("CMP-15")
def test_older_failed_ideas_are_capped():
    attempts = [_attempt(n, hypothesis=f"idea number {n}") for n in range(1, 60)]
    patterns = _patterns_section(render_brief(_campaign(), attempts, cycle_no=60, now=NOW))
    assert "idea number 54" in patterns  # newest older cycle (55–59 are recent)
    assert "idea number 25" in patterns
    assert "idea number 24" not in patterns  # 30-line cap
    assert "older" in patterns  # the cut is announced, with where to read the rest
