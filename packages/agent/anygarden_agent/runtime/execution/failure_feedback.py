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
    return None
