"""Codex reports cumulative session counters, not invocation token usage."""

from __future__ import annotations

import hashlib

SOURCE = "codex-session-cumulative-v1"
TOKEN_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens")
MAX_COUNTER = (1 << 63) - 1
STATUSES = frozenset({
    "fresh_measured", "measured_delta", "baseline_missing", "baseline_invalidated",
    "counter_rollback", "session_changed", "session_unobserved",
    "terminal_usage_missing", "provider_usage_invalid", "process_outcome_unknown",
    "not_started",
})
BASELINE_STATUSES = frozenset({"fresh", "valid", "absent", "invalidated"})


def counters(value: object) -> dict[str, int | None] | None:
    """Retain bounded numeric provider evidence, never arbitrary provider text."""
    if not isinstance(value, dict):
        return None
    return {
        key: number if type(number := value.get(key)) is int and 0 <= number <= MAX_COUNTER else None
        for key in TOKEN_KEYS
    }


def digest(value: str | None) -> str | None:
    return hashlib.sha256(value.encode()).hexdigest() if value else None


def invocation_usage(
    *, requested_session: str | None, observed_session: str | None,
    provider_cumulative: object, terminal_usage: bool, process_state: str,
    baseline: dict | None,
) -> dict:
    """Only a complete terminal counter with a stable baseline yields a delta.

    A missing invocation invalidates its session baseline durably in the store.
    A later terminal counter establishes a new baseline, but that invocation
    remains unknown because it also includes the missing invocation's usage.
    """
    raw = counters(provider_cumulative)
    prior = counters(baseline.get("cumulative")) if baseline else None
    baseline_status = (
        "fresh" if requested_session is None else "absent" if baseline is None
        else "valid" if baseline["valid"] else "invalidated"
    )
    measured = False
    if process_state == "not_started":
        status = "not_started"
    elif process_state == "unknown":
        status = "process_outcome_unknown"
    elif not terminal_usage:
        status = "terminal_usage_missing"
    elif raw is None or raw["input_tokens"] is None or raw["output_tokens"] is None:
        status = "provider_usage_invalid"
    elif observed_session is None:
        status = "session_unobserved"
    elif requested_session is not None and observed_session != requested_session:
        status = "session_changed"
    elif requested_session is None:
        status, measured = "fresh_measured", True
        prior = {key: 0 for key in TOKEN_KEYS}
    elif baseline_status != "valid" or prior is None:
        status = "baseline_invalidated" if baseline_status == "invalidated" else "baseline_missing"
    elif (prior["input_tokens"] is None or prior["output_tokens"] is None
          or any(raw[key] is not None and prior[key] is not None and raw[key] < prior[key] for key in TOKEN_KEYS)):
        status = "counter_rollback"
    else:
        status, measured = "measured_delta", True
    usage = {
        key: raw[key] - prior[key] if measured and raw[key] is not None and prior[key] is not None else None
        for key in TOKEN_KEYS
    }
    usage["metadata"] = {
        "source": SOURCE, "status": status, "baseline_status": baseline_status,
        "provider_cumulative": raw, "baseline": prior,
        "requested_session_sha256": digest(requested_session),
        "observed_session_sha256": digest(observed_session),
        "baseline_execution_sha256": digest(baseline.get("execution_id")) if baseline else None,
    }
    return usage
