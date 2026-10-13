"""Project CLI error messages onto a small, non-sensitive failure vocabulary."""

from __future__ import annotations

import json
import os
import re
from typing import Any

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
    # Codex transport failures (live benchmark 2026-10-12).
    "stream disconnected", "timed out", "internal server error",
    # "Selected model is at capacity. Please try a different model." (live
    # benchmark 2026-10-13). Still never transient after a side effect.
    "model is at capacity", "at capacity",
)
# ``"status":400`` in codex's structured error, or ``unexpected status 429``.
_STATUS_TEXT = re.compile(r"\bstatus(?:\s+code)?[\s:=]+(\d{3})\b")


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
        status = _codex_status(message)
        if status is not None:
            # A provider status is authoritative: words like "timeout" inside
            # a 400 invalid_request body must not make it retryable.
            transient = _transient_status(status)
        else:
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


# -- codex-cli turn failure diagnosis ----------------------------------------
#
# ``codex exec --json`` reports provider failures as JSONL ``turn.failed`` /
# top-level ``error`` events whose message is often itself a JSON document
# (``{"type":"error","status":429,"error":{"type":...,"message":...}}``).
# The tracker below derives (a) a short, redacted human reason for the LOCAL
# agent log and EngineError text, and (b) whether the failure is safe to
# retry automatically. Receipts and peers still only ever see the closed code.

REASON_MAX_CHARS = 300

# Item types that never change anything outside the model conversation. Any
# other item (command_execution, file_change, mcp_tool_call, unknown/missing
# type) counts as a side effect: once one has started, re-running the turn
# could repeat it, so the failure is never transient.
SIDE_EFFECT_FREE_ITEMS = frozenset(
    {"agent_message", "reasoning", "error", "todo_list", "plan", "web_search"}
)

_REDACTED = "[REDACTED]"
_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer " + _REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), _REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), _REDACTED),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|authorization)"
            r"(\s*[:=]\s*)[^\s,;\"']+"
        ),
        r"\1\2" + _REDACTED,
    ),
    # Long opaque strings (keys, signed URLs' signatures).
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), _REDACTED),
)
_TOKEN_ENV = ("ANYGARDEN_AGENT_TOKEN", "ANYGARDEN_TOKEN")


def _known_secret_values() -> list[str]:
    values = [os.environ.get(name) for name in _TOKEN_ENV]
    from anygarden_agent import secrets as _secrets

    values += list(_secrets.all_secrets().values())
    return sorted({v for v in values if isinstance(v, str) and len(v) >= 8}, key=len, reverse=True)


def redact_secrets(text: str) -> str:
    """Mask the agent token, staged engine secrets and secret-looking strings."""
    for value in _known_secret_values():
        text = text.replace(value, _REDACTED)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _transient_status(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


def _structured_error(message: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(message)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _codex_status(message: str) -> int | None:
    obj = _structured_error(message)
    if obj is not None:
        status = obj.get("status")
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            return status
    match = _STATUS_TEXT.search(message.casefold())
    return int(match.group(1)) if match else None


def _summarize(message: str) -> str:
    obj = _structured_error(message)
    parts: list[str] = []
    status = _codex_status(message)
    if status is not None:
        parts.append(f"status {status}")
    if obj is not None:
        inner = obj.get("error")
        if isinstance(inner, dict):
            for key in ("type", "code", "message"):
                value = inner.get(key)
                if isinstance(value, str) and value and value not in parts:
                    parts.append(value)
        elif isinstance(obj.get("message"), str):
            parts.append(obj["message"])
    if len(parts) <= (1 if status is not None else 0):
        parts.append(message)
    text = " ".join(redact_secrets(": ".join(parts)).split())
    if len(text) > REASON_MAX_CHARS:
        text = text[: REASON_MAX_CHARS - 1] + "…"
    return text


class CodexFailureTracker:
    """Observe one codex turn's JSONL events and diagnose how it failed."""

    def __init__(self) -> None:
        self.side_effect_started = False
        self._turn_failures: list[str | None] = []
        self._last_error: str | None = None

    def observe(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            if item_type not in SIDE_EFFECT_FREE_ITEMS:
                self.side_effect_started = True
        elif kind == "turn.failed":
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            self._turn_failures.append(message if isinstance(message, str) else None)
        elif kind == "error":
            message = event.get("message")
            if isinstance(message, str) and message:
                self._last_error = message

    @property
    def message(self) -> str | None:
        """Raw failure message — local diagnosis only, never a receipt/peer."""
        if self._turn_failures:
            return self._turn_failures[0]
        return self._last_error

    @property
    def status(self) -> int | None:
        message = self.message
        return _codex_status(message) if message else None

    @property
    def reason(self) -> str | None:
        message = self.message
        return _summarize(message) if message else None

    def code(self) -> str | None:
        """Closed failure code, transient only before any side effect."""
        if len(self._turn_failures) > 1:
            return None  # ambiguous: more than one terminal failure
        code = classify_failure("codex-cli", self.message)
        if code == "MODEL_TRANSIENT_FAILURE" and self.side_effect_started:
            return None
        return code

    @property
    def transient(self) -> bool:
        return self.code() == "MODEL_TRANSIENT_FAILURE"


def stderr_tail(raw: bytes | bytearray | str, limit: int = 500) -> str:
    """Last ``limit`` chars of a CLI's stderr, redacted, for the local log."""
    text = raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
    # Bound the redaction work, then cut to the final limit.
    text = redact_secrets(text[-limit * 4:].strip())
    return text[-limit:]
