"""Cycle scoring (#930 §3.3): outcome classification per metric direction."""

from __future__ import annotations

import pytest

from anygarden.campaigns.judge import beats, classify, reaches_target

MAX = {"name": "acc", "direction": "maximize", "target": 0.9, "min_delta": 0.01}
MIN = {"name": "loss", "direction": "minimize", "target": 0.1, "min_delta": 0.0}


def _ok(score, valid=True, key="score"):
    return {"status": "ok", "parsed": {key: score, "valid": valid}, "score_key": key}


@pytest.mark.parametrize(
    ("metric", "score", "best", "outcome"),
    [
        (MAX, 0.5, None, "improved"),  # the first valid score improves
        (MAX, 0.52, 0.5, "improved"),
        (MAX, 0.505, 0.5, "no_gain"),  # inside min_delta
        (MAX, 0.51, 0.5, "no_gain"),  # exactly min_delta is not "beyond"
        (MAX, 0.5, 0.5, "no_gain"),
        (MAX, 0.4, 0.5, "regressed"),
        (MIN, 0.3, 0.4, "improved"),
        (MIN, 0.4, 0.4, "no_gain"),  # min_delta 0 still needs a strict gain
        (MIN, 0.5, 0.4, "regressed"),
    ],
)
def test_direction_and_min_delta(metric, score, best, outcome):
    verdict = classify(metric=metric, best_score=best, **_ok(score))
    assert verdict.outcome == outcome
    assert verdict.eval_status == "ok" and verdict.dev_score == pytest.approx(score)


def test_invalid_score_is_a_guardrail_violation_even_if_better():
    verdict = classify(metric=MAX, best_score=0.5, **_ok(0.99, valid=False))
    assert (verdict.outcome, verdict.valid, verdict.dev_score) == ("guardrail_violation", False, 0.99)
    assert not verdict.improved


@pytest.mark.parametrize(
    ("status", "parsed", "outcome", "eval_status"),
    [
        ("failed", None, "eval_failed", "failed"),
        ("timed_out", None, "eval_failed", "timed_out"),
        ("invalid", None, "invalid", "invalid"),
        ("ok", {"valid": True}, "eval_failed", "failed"),
        ("ok", {"score": "high", "valid": True}, "eval_failed", "failed"),
        ("ok", {"score": 1.0}, "eval_failed", "failed"),
    ],
)
def test_failures(status, parsed, outcome, eval_status):
    verdict = classify(metric=MAX, score_key="score", status=status, parsed=parsed, best_score=None)
    assert (verdict.outcome, verdict.eval_status) == (outcome, eval_status)


def test_custom_score_key():
    verdict = classify(metric=MAX, best_score=None, **_ok(0.7, key="acc"))
    assert verdict.outcome == "improved" and verdict.dev_score == 0.7


def test_target_and_beats_helpers():
    assert reaches_target(0.9, MAX) and not reaches_target(0.89, MAX)
    assert reaches_target(0.1, MIN) and not reaches_target(0.11, MIN)
    assert not reaches_target(None, MAX)
    assert not reaches_target(1.0, {**MAX, "target": None})
    assert beats(0.1, None, MAX)
