"""Project CLI error messages onto a small, non-sensitive failure vocabulary."""

from __future__ import annotations

import re

MAX_FAILURE_MESSAGE_BYTES = 4096

_AUTH_SIGNALS = (
    "not logged in",
    "login required",
    "run codex login",
    "authentication failed",
    "invalid authentication",
    "missing bearer authentication",
    "incorrect api key",
    "invalid api key",
    "api key is invalid",
)
_AUTH_STATUS = re.compile(r"\b401(?::|\s+unauthorized\b)")
_PROVIDER_SIGNALS = (
    re.compile(r"\bunknown provider\b"),
    re.compile(r"\bprovider\b.{1,64}\bnot found\b"),
)
_MODEL_SIGNALS = (
    re.compile(r"\bmodel\b.{0,160}\b(?:not supported|not found|does not exist|invalid|unknown)\b"),
    re.compile(r"\b(?:unsupported|invalid|unknown)\s+model\b"),
)
_TRANSIENT_STATUS = re.compile(r"\b(?:429|500|502|503|504)\b")
_TRANSIENT_SIGNALS = (
    "rate limit", "too many requests", "service unavailable", "overloaded",
    "bad gateway", "gateway timeout", "connection reset", "connection refused",
    "connection aborted", "connection error", "temporarily unavailable",
)


def classify_failure(engine: str, message: object) -> str | None:
    """Return a safe code only when one failure category is unambiguous.

    The caller must discard the raw message. It can contain provider keys,
    URLs and account information, so it must never reach a receipt or peer.
    """
    if not isinstance(message, str) or not message:
        return None
    if len(message.encode("utf-8")) > MAX_FAILURE_MESSAGE_BYTES:
        return None
    normalized = message.casefold()
    if re.search(r"\b403(?:\b|:)", normalized):
        return None
    auth = any(signal in normalized for signal in _AUTH_SIGNALS) or bool(
        _AUTH_STATUS.search(normalized)
    )
    provider = engine == "pi-cli" and any(
        pattern.search(normalized) for pattern in _PROVIDER_SIGNALS
    )
    if auth and not provider:
        return "ENGINE_AUTH_ERROR"
    if provider and not auth:
        return "PI_PROVIDER_ERROR"
    if auth or provider:
        return None
    # Codex emits structured turn.failed errors for these categories. Keep
    # only the closed category; provider URLs, model names and credentials
    # in the raw message never become receipt or lifecycle text.
    if engine == "codex-cli":
        model = any(pattern.search(normalized) for pattern in _MODEL_SIGNALS)
        transient = bool(_TRANSIENT_STATUS.search(normalized)) or any(
            signal in normalized for signal in _TRANSIENT_SIGNALS
        )
        if model and not transient:
            return "MODEL_CONFIGURATION_INVALID"
        if transient and not model:
            return "MODEL_TRANSIENT_FAILURE"
    return None


def terminal_failure_category(outcome: str | None, reason: str | None) -> tuple[str | None, bool]:
    """Project a persisted terminal receipt without trusting raw error text."""
    if outcome == "unknown":
        return "PROCESS_OUTCOME_UNKNOWN", False
    if outcome != "failed":
        return None, False
    if reason in {"ENGINE_AUTH_ERROR", "AUTH_MISSING", "AUTH_CHECK_FAILED"}:
        return "AUTHENTICATION_FAILED", False
    if reason in {
        "MODEL_CONFIGURATION_INVALID", "PI_PROVIDER_ERROR", "UNKNOWN_PROVIDER",
        "UNSUPPORTED_RUNTIME", "POLICY_DENIED",
    }:
        return "MODEL_CONFIGURATION_INVALID", False
    if reason in {"MODEL_TRANSIENT_FAILURE", "TIMEOUT_STOPPED"}:
        return "MODEL_TRANSIENT_FAILURE", True
    return "MODEL_EXECUTION_FAILED", False
