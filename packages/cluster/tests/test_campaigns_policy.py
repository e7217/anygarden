"""Campaign creation rules (#930; plan CMP-01)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anygarden.campaigns.policy import (
    BRIEF_MAX_CHARS,
    EVAL_FAILURE_BLOCK_THRESHOLD,
    MAX_EVAL_TIMEOUT_SECONDS,
    MIN_GAP_SECONDS,
    PLATEAU_WINDOW,
    RETRO_EVERY_CYCLES,
    CampaignPolicyError,
    has_token_budget,
    usable_tokens,
    validate_campaign_spec,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _spec(**overrides):
    args = {
        "title": "Raise the benchmark score",
        "purpose": "Make the solver good enough to enter the contest.",
        "mode": "target",
        "metric": {"name": "accuracy", "direction": "maximize", "target": 0.9},
        "evaluator": {"command": "python bench.py", "timeout_sec": 600},
        "budget": {"max_total_tokens": 1_000_000},
        "deadline_at": None,
        "trusted_eval": "none",
        "now": NOW,
    }
    args.update(overrides)
    return validate_campaign_spec(**args)


def _code(**overrides) -> str:
    with pytest.raises(CampaignPolicyError) as exc:
        _spec(**overrides)
    return exc.value.code


def test_constants_match_the_plan():
    assert MIN_GAP_SECONDS == 300
    assert PLATEAU_WINDOW == 5
    assert RETRO_EVERY_CYCLES == 10
    assert EVAL_FAILURE_BLOCK_THRESHOLD == 3
    assert BRIEF_MAX_CHARS == 12_000
    assert MAX_EVAL_TIMEOUT_SECONDS == 6 * 3600


def test_valid_target_spec_is_normalised_with_defaults():
    spec = _spec()
    assert spec.mode == "target"
    assert spec.metric == {
        "name": "accuracy", "direction": "maximize", "target": 0.9, "min_delta": 0.0,
    }
    assert spec.evaluator == {
        "command": "python bench.py", "cwd": ".", "timeout_sec": 600,
        "pin_paths": [], "score_key": "score",
    }
    assert spec.budget == {"max_total_tokens": 1_000_000, "retro_reserve_ratio": 0.05}
    assert spec.trusted_eval == "none"
    assert spec.deadline_at is None


@pytest.mark.parametrize("budget", [None, {}, {"retro_reserve_ratio": 0.1},
                                    {"max_total_tokens": None}])
@pytest.mark.req("CMP-01")
def test_token_budget_is_optional(budget):
    # #963: no token budget is the default; the goal ends by target,
    # plateau, eval failures, deadline or a person instead.
    spec = _spec(budget=budget)
    assert "max_total_tokens" not in spec.budget
    assert spec.budget["retro_reserve_ratio"] == (
        0.1 if budget and budget.get("retro_reserve_ratio") else 0.05
    )
    assert usable_tokens(spec.budget) is None
    assert has_token_budget(spec.budget) is False


@pytest.mark.parametrize("budget", [{"max_total_tokens": 0},
                                    {"max_total_tokens": -5}, {"max_total_tokens": True},
                                    {"max_total_tokens": "100"}, "x", 5])
@pytest.mark.req("CMP-01")
def test_a_given_token_budget_must_be_a_positive_integer(budget):
    assert _code(budget=budget) == "INVALID_BUDGET"


@pytest.mark.parametrize("ratio", [-0.1, 0.6, "x"])
def test_retro_reserve_ratio_is_checked_without_a_token_budget(ratio):
    assert _code(budget={"retro_reserve_ratio": ratio}) == "INVALID_BUDGET"


@pytest.mark.parametrize("ratio", [-0.1, 0.6, "x"])
def test_retro_reserve_ratio_range(ratio):
    assert _code(budget={"max_total_tokens": 10, "retro_reserve_ratio": ratio}) == "INVALID_BUDGET"


@pytest.mark.req("CMP-01")
def test_target_or_deadline_is_required():
    # Target mode without a target, and no deadline either.
    assert _code(metric={"name": "m", "direction": "maximize"}) == "TARGET_OR_DEADLINE_REQUIRED"


@pytest.mark.req("CMP-01")
def test_target_mode_requires_a_target_even_with_a_deadline():
    assert _code(
        metric={"name": "m", "direction": "maximize"},
        deadline_at=NOW + timedelta(days=1),
    ) == "TARGET_REQUIRED"


def test_target_mode_with_target_and_deadline_is_valid():
    spec = _spec(deadline_at=NOW + timedelta(days=3))
    assert spec.deadline_at == NOW + timedelta(days=3)


@pytest.mark.req("CMP-01")
def test_optimize_mode_requires_a_deadline():
    assert _code(
        mode="optimize", metric={"name": "m", "direction": "minimize"}
    ) == "TARGET_OR_DEADLINE_REQUIRED"


@pytest.mark.req("CMP-01")
def test_optimize_mode_rejects_a_target():
    assert _code(
        mode="optimize", metric={"name": "m", "direction": "minimize", "target": 1.0},
        deadline_at=NOW + timedelta(days=1),
    ) == "OPTIMIZE_HAS_NO_TARGET"


def test_optimize_mode_with_deadline_is_valid():
    spec = _spec(
        mode="optimize", metric={"name": "loss", "direction": "minimize"},
        deadline_at=NOW + timedelta(days=1),
    )
    assert spec.metric["target"] is None


@pytest.mark.parametrize("mode", ["", "loop", None])
def test_mode_enum(mode):
    assert _code(mode=mode) == "INVALID_MODE"


@pytest.mark.parametrize("metric", [
    None, {}, {"name": "", "direction": "maximize", "target": 1},
    {"name": "m", "direction": "up", "target": 1},
    {"name": "m", "direction": "maximize", "target": "high"},
    {"name": "m", "direction": "maximize", "target": 1, "min_delta": -1},
])
def test_metric_shape(metric):
    assert _code(metric=metric) == "INVALID_METRIC"


@pytest.mark.parametrize("trusted", ["", "command", None])
def test_trusted_eval_enum(trusted):
    assert _code(trusted_eval=trusted) == "INVALID_TRUSTED_EVAL"


@pytest.mark.parametrize("evaluator", [
    None, {}, {"command": "  "},
    {"command": "x", "timeout_sec": 0},
    {"command": "x", "timeout_sec": 6 * 3600 + 1},
    {"command": "x", "timeout_sec": True},
    {"command": "x", "cwd": "/etc"},
    {"command": "x", "cwd": "../outside"},
    {"command": "x", "pin_paths": "bench.py"},
    {"command": "x", "pin_paths": ["../escape.py"]},
    {"command": "x", "pin_paths": ["/abs.py"]},
    {"command": "x", "score_key": ""},
])
def test_evaluator_shape(evaluator):
    assert _code(evaluator=evaluator) == "INVALID_EVALUATOR"


def test_evaluator_timeout_may_be_six_hours():
    assert _spec(evaluator={"command": "x", "timeout_sec": 6 * 3600}).evaluator["timeout_sec"] == 21600


def test_deadline_must_be_aware_and_future():
    assert _code(deadline_at=NOW - timedelta(seconds=1)) == "INVALID_DEADLINE"
    assert _code(deadline_at=datetime(2027, 1, 1))  # noqa: DTZ001 — naive on purpose == "INVALID_DEADLINE"


@pytest.mark.parametrize("title,purpose", [("", "p"), ("t", "  "), ("x" * 501, "p")])
def test_title_and_purpose_required(title, purpose):
    assert _code(title=title, purpose=purpose) == "INVALID_CAMPAIGN"


@pytest.mark.req("CMP-10")
def test_usable_tokens_keeps_the_retro_reserve():
    assert usable_tokens({"max_total_tokens": 1000, "retro_reserve_ratio": 0.05}) == 950
    assert usable_tokens({"max_total_tokens": 1000}) == 950
    assert usable_tokens({"max_total_tokens": 1000, "retro_reserve_ratio": 0}) == 1000
    assert has_token_budget({"max_total_tokens": 1000}) is True
    assert has_token_budget({"retro_reserve_ratio": 0.05}) is False
    assert has_token_budget(None) is False
