"""Scoring a cycle (#930 §3.3): pure functions, no DB.

``classify`` turns one machine evaluation result into an attempt outcome:

- ``invalid`` — the pinned evaluator files changed (pin mismatch, CMP-05),
- ``eval_failed`` — the evaluator failed, timed out or printed no score,
- ``guardrail_violation`` — a score with ``valid=false``,
- ``improved`` — a valid score that beats the best so far by more than
  ``metric.min_delta`` in the metric's direction (the first valid score
  always improves),
- ``regressed`` — a valid score worse than the best,
- ``no_gain`` — a valid score that is not worse but not enough better.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_EPSILON = 1e-9

JUDGED_OUTCOMES: frozenset[str] = frozenset(
    {"improved", "no_gain", "regressed", "guardrail_violation"}
)
"""Outcomes of a scored cycle; these count towards the plateau window."""


@dataclass(frozen=True)
class Verdict:
    outcome: str
    eval_status: str
    """The attempt's ``eval_status``: ok | failed | invalid | timed_out."""
    dev_score: float | None = None
    valid: bool | None = None

    @property
    def improved(self) -> bool:
        return self.outcome == "improved"

    @property
    def eval_failed(self) -> bool:
        return self.outcome == "eval_failed"

    @property
    def pin_mismatch(self) -> bool:
        return self.outcome == "invalid"


def gain(score: float, best: float, direction: str) -> float:
    """How much *score* is better than *best* (negative when worse)."""
    return score - best if direction == "maximize" else best - score


def beats(score: float, best: float | None, metric: dict[str, Any]) -> bool:
    if best is None:
        return True
    # A float tolerance so ``0.51 - 0.5`` does not count as beating 0.01.
    min_delta = float(metric.get("min_delta") or 0.0)
    return gain(score, best, metric["direction"]) > min_delta + _EPSILON * max(1.0, abs(best))


def reaches_target(score: float | None, metric: dict[str, Any]) -> bool:
    target = metric.get("target")
    if score is None or target is None:
        return False
    return score >= target if metric["direction"] == "maximize" else score <= target


def classify(
    *,
    metric: dict[str, Any],
    score_key: str,
    status: str,
    parsed: dict[str, Any] | None,
    best_score: float | None,
) -> Verdict:
    """Classify one evaluation result against the best valid score so far."""
    if status == "invalid":
        return Verdict("invalid", "invalid")
    if status != "ok" or not parsed:
        return Verdict("eval_failed", "timed_out" if status == "timed_out" else "failed")
    score = parsed.get(score_key)
    valid = parsed.get("valid")
    if isinstance(score, bool) or not isinstance(score, int | float) or not isinstance(valid, bool):
        return Verdict("eval_failed", "failed")
    score = float(score)
    if not valid:
        return Verdict("guardrail_violation", "ok", score, False)
    if beats(score, best_score, metric):
        return Verdict("improved", "ok", score, True)
    if best_score is not None and gain(score, best_score, metric["direction"]) < 0:
        return Verdict("regressed", "ok", score, True)
    return Verdict("no_gain", "ok", score, True)


__all__ = ["JUDGED_OUTCOMES", "Verdict", "beats", "classify", "gain", "reaches_target"]
