"""Campaign rules (#930): constants, vocabularies and creation validation.

Pure — no DB, no app imports — so the API, MCP tools and the scheduler
can all share it. ``validate_campaign_spec`` returns the normalised
criteria that get stored on :class:`anygarden.db.models.Campaign`.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

# ── Loop constants ──────────────────────────────────────────────────

MIN_GAP_SECONDS: int = 300
"""Minimum time between a cycle closing and the next one opening."""

PLATEAU_WINDOW: int = 5
"""Cycles without improvement before a target campaign plateaus (an
optimize campaign is told to change strategy instead)."""

RETRO_EVERY_CYCLES: int = 10
"""A retrospective cycle runs every N cycles and when the loop ends."""

EVAL_FAILURE_BLOCK_THRESHOLD: int = 3
"""Consecutive evaluation failures that block the campaign."""

BRIEF_MAX_CHARS: int = 12_000
"""Upper bound on a cycle brief (the cycle Task's spec)."""

MAX_EVAL_TIMEOUT_SECONDS: int = 6 * 3600
DEFAULT_EVAL_TIMEOUT_SECONDS: int = 600
DEFAULT_RETRO_RESERVE_RATIO: float = 0.05
MAX_EVAL_COMMAND_CHARS: int = 8192
MAX_EVAL_PATH_CHARS: int = 1024
MAX_EVAL_PIN_PATHS: int = 100
MAX_SCORE_KEY_CHARS: int = 120
"""Evaluator limits — the same as ``CampaignEvalRequestFrame``'s, so an
evaluator the server accepts is one the machine can parse."""
MAX_RETRO_RESERVE_RATIO: float = 0.5

# ── Vocabularies ────────────────────────────────────────────────────

MODES: frozenset[str] = frozenset({"target", "optimize"})
DIRECTIONS: frozenset[str] = frozenset({"maximize", "minimize"})
TRUSTED_EVAL_MODES: frozenset[str] = frozenset({"none", "external"})

OPEN_STATUSES: frozenset[str] = frozenset(
    {"active", "paused", "blocked", "plateau", "awaiting_confirmation"}
)
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"achieved", "deadline_reached", "budget_exhausted", "stopped"}
)
STATUSES: frozenset[str] = OPEN_STATUSES | TERMINAL_STATUSES

ATTEMPT_KINDS: frozenset[str] = frozenset({"cycle", "retrospective"})
AGENT_OUTCOMES: frozenset[str] = frozenset({"attempted", "abandoned"})
EVAL_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "ok", "failed", "invalid", "skipped", "timed_out"}
)
OUTCOMES: frozenset[str] = frozenset(
    {"improved", "no_gain", "regressed", "guardrail_violation", "eval_failed",
     "invalid", "no_record", "unknown"}
)


class CampaignPolicyError(ValueError):
    """A campaign request breaks a rule. ``code`` is stable for API errors."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CampaignSpec:
    title: str
    purpose: str
    mode: str
    metric: dict[str, Any]
    evaluator: dict[str, Any]
    trusted_eval: str
    budget: dict[str, Any]
    deadline_at: datetime | None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _relative_path(value: Any) -> bool:
    """A non-empty path that stays inside the workspace."""
    if (
        not isinstance(value, str) or not value.strip() or "\x00" in value
        or len(value) > MAX_EVAL_PATH_CHARS
    ):
        return False
    path = PurePosixPath(value.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def _metric(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CampaignPolicyError("INVALID_METRIC", "metric must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise CampaignPolicyError("INVALID_METRIC", "metric.name is required")
    direction = raw.get("direction")
    if direction not in DIRECTIONS:
        raise CampaignPolicyError(
            "INVALID_METRIC", "metric.direction must be maximize or minimize"
        )
    target = raw.get("target")
    if target is not None and not _is_number(target):
        raise CampaignPolicyError("INVALID_METRIC", "metric.target must be a number")
    min_delta = raw.get("min_delta", 0.0)
    if not _is_number(min_delta) or min_delta < 0:
        raise CampaignPolicyError(
            "INVALID_METRIC", "metric.min_delta must be a non-negative number"
        )
    return {
        "name": name.strip(),
        "direction": direction,
        "target": float(target) if target is not None else None,
        "min_delta": float(min_delta),
    }


def _evaluator(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CampaignPolicyError("INVALID_EVALUATOR", "evaluator must be an object")
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise CampaignPolicyError("INVALID_EVALUATOR", "evaluator.command is required")
    if len(command.strip()) > MAX_EVAL_COMMAND_CHARS:
        raise CampaignPolicyError(
            "INVALID_EVALUATOR",
            f"evaluator.command is longer than {MAX_EVAL_COMMAND_CHARS} characters",
        )
    cwd = raw.get("cwd", ".")
    if not _relative_path(cwd):
        raise CampaignPolicyError(
            "INVALID_EVALUATOR", "evaluator.cwd must be a path inside the workspace"
        )
    timeout = raw.get("timeout_sec", DEFAULT_EVAL_TIMEOUT_SECONDS)
    if not _is_int(timeout) or not 1 <= timeout <= MAX_EVAL_TIMEOUT_SECONDS:
        raise CampaignPolicyError(
            "INVALID_EVALUATOR",
            f"evaluator.timeout_sec must be 1..{MAX_EVAL_TIMEOUT_SECONDS}",
        )
    pin_paths = raw.get("pin_paths", [])
    if not isinstance(pin_paths, list) or not all(_relative_path(p) for p in pin_paths):
        raise CampaignPolicyError(
            "INVALID_EVALUATOR", "evaluator.pin_paths must be workspace paths"
        )
    if len(pin_paths) > MAX_EVAL_PIN_PATHS:
        raise CampaignPolicyError(
            "INVALID_EVALUATOR", f"evaluator.pin_paths takes at most {MAX_EVAL_PIN_PATHS} paths"
        )
    score_key = raw.get("score_key", "score")
    if not isinstance(score_key, str) or not score_key.strip():
        raise CampaignPolicyError("INVALID_EVALUATOR", "evaluator.score_key is required")
    if len(score_key.strip()) > MAX_SCORE_KEY_CHARS:
        raise CampaignPolicyError(
            "INVALID_EVALUATOR",
            f"evaluator.score_key is longer than {MAX_SCORE_KEY_CHARS} characters",
        )
    return {
        "command": command.strip(),
        "cwd": cwd,
        "timeout_sec": timeout,
        "pin_paths": list(pin_paths),
        "score_key": score_key.strip(),
    }


def _budget(raw: Any) -> dict[str, Any]:
    """Normalise a budget. The token cap is optional (#963): without
    ``max_total_tokens`` the goal never ends on tokens."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise CampaignPolicyError("INVALID_BUDGET", "budget must be an object")
    ratio = raw.get("retro_reserve_ratio", DEFAULT_RETRO_RESERVE_RATIO)
    if not _is_number(ratio) or not 0 <= ratio <= MAX_RETRO_RESERVE_RATIO:
        raise CampaignPolicyError(
            "INVALID_BUDGET",
            f"budget.retro_reserve_ratio must be 0..{MAX_RETRO_RESERVE_RATIO}",
        )
    out: dict[str, Any] = {"retro_reserve_ratio": float(ratio)}
    cap = raw.get("max_total_tokens")
    if cap is not None:
        if not _is_int(cap) or cap <= 0:
            raise CampaignPolicyError(
                "INVALID_BUDGET", "budget.max_total_tokens must be a positive integer"
            )
        out = {"max_total_tokens": cap, **out}
    return out


def validate_campaign_spec(
    *,
    title: Any,
    purpose: Any,
    mode: Any,
    metric: Any,
    evaluator: Any,
    budget: Any,
    deadline_at: datetime | None = None,
    trusted_eval: Any = "none",
    now: datetime,
) -> CampaignSpec:
    """Check a campaign request and return its normalised criteria (CMP-01).

    - ``budget.max_total_tokens`` is optional (#963); when given it is a
      positive integer. Without it the goal never ends on tokens.
    - A target mode campaign needs ``metric.target``; an optimize campaign
      needs ``deadline_at`` and has no target. Either way the loop has an
      end: a target or a deadline.
    """
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 500:
        raise CampaignPolicyError("INVALID_CAMPAIGN", "title is required (≤ 500 chars)")
    if not isinstance(purpose, str) or not purpose.strip():
        raise CampaignPolicyError("INVALID_CAMPAIGN", "purpose is required")
    if mode not in MODES:
        raise CampaignPolicyError("INVALID_MODE", "mode must be target or optimize")
    if trusted_eval not in TRUSTED_EVAL_MODES:
        raise CampaignPolicyError(
            "INVALID_TRUSTED_EVAL", "trusted_eval must be none or external"
        )
    norm_metric = _metric(metric)
    norm_evaluator = _evaluator(evaluator)
    norm_budget = _budget(budget)
    if deadline_at is not None and (
        deadline_at.tzinfo is None
        or deadline_at.utcoffset() is None
        or deadline_at <= now
    ):
        raise CampaignPolicyError(
            "INVALID_DEADLINE", "deadline_at must be a future timestamp with timezone"
        )
    if norm_metric["target"] is None and deadline_at is None:
        raise CampaignPolicyError(
            "TARGET_OR_DEADLINE_REQUIRED",
            "a campaign needs a metric target or a deadline",
        )
    if mode == "target" and norm_metric["target"] is None:
        raise CampaignPolicyError("TARGET_REQUIRED", "target mode needs metric.target")
    if mode == "optimize" and norm_metric["target"] is not None:
        raise CampaignPolicyError(
            "OPTIMIZE_HAS_NO_TARGET",
            "optimize mode runs until its deadline and takes no target",
        )
    return CampaignSpec(
        title=title.strip(),
        purpose=purpose.strip(),
        mode=mode,
        metric=norm_metric,
        evaluator=norm_evaluator,
        trusted_eval=trusted_eval,
        budget=norm_budget,
        deadline_at=deadline_at,
    )


def evaluator_digest(evaluator: dict[str, Any] | None) -> str:
    """Stable sha256 of an evaluator, to tell which evaluator a result
    came from after the campaign's evaluator was edited."""
    encoded = json.dumps(evaluator or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def has_token_budget(budget: dict[str, Any] | None) -> bool:
    """Whether the goal has a token budget (#963: it is optional)."""
    return bool(budget) and budget.get("max_total_tokens") is not None


def usable_tokens(budget: dict[str, Any] | None) -> int | None:
    """Tokens cycles may spend; the rest is reserved for the retrospective.

    ``None`` when the goal has no token budget: tokens never end it.
    """
    if not has_token_budget(budget):
        return None
    assert budget is not None
    cap = int(budget["max_total_tokens"])
    ratio = float(budget.get("retro_reserve_ratio", DEFAULT_RETRO_RESERVE_RATIO))
    return int(cap * (1 - ratio))


def cycle_tokens_spent(budget: dict[str, Any] | None, tokens_used: int) -> bool:
    """Whether the cycles used up their tokens; never without a budget."""
    usable = usable_tokens(budget)
    return usable is not None and usable - int(tokens_used or 0) <= 0


__all__ = [
    "AGENT_OUTCOMES",
    "ATTEMPT_KINDS",
    "BRIEF_MAX_CHARS",
    "DEFAULT_EVAL_TIMEOUT_SECONDS",
    "DEFAULT_RETRO_RESERVE_RATIO",
    "DIRECTIONS",
    "EVAL_FAILURE_BLOCK_THRESHOLD",
    "EVAL_STATUSES",
    "MAX_EVAL_COMMAND_CHARS",
    "MAX_EVAL_PATH_CHARS",
    "MAX_EVAL_PIN_PATHS",
    "MAX_EVAL_TIMEOUT_SECONDS",
    "MAX_RETRO_RESERVE_RATIO",
    "MAX_SCORE_KEY_CHARS",
    "MIN_GAP_SECONDS",
    "MODES",
    "OPEN_STATUSES",
    "OUTCOMES",
    "PLATEAU_WINDOW",
    "RETRO_EVERY_CYCLES",
    "STATUSES",
    "TERMINAL_STATUSES",
    "TRUSTED_EVAL_MODES",
    "CampaignPolicyError",
    "CampaignSpec",
    "cycle_tokens_spent",
    "evaluator_digest",
    "has_token_budget",
    "usable_tokens",
    "validate_campaign_spec",
]
