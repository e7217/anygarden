"""Ambient room-context capture policy (#74 Stage B).

Stage A shipped the flag-based ingest path: a message tagged with
``metadata.ingest_only=True`` (canonically a ``[취합 결과]``
broadcast) is absorbed into the engine session context instead of
silently dropped. That handles structural events — but it doesn't
cover the "agent follows the natural room flow" case: humans
chatting, other agents replying to the human, mentions directed
at a peer. Those messages still fail the response gate and never
reach the engine SDK.

Stage B adds a sliding-window policy on top of the same
``ingest_context`` pipeline. When enabled, unflagged ambient
messages that would otherwise SKIP are promoted to INGEST_ONLY
and buffered for the next active turn.

This module intentionally owns only the *policy* — "should this
message count as ambient?". The *storage* stays in each adapter's
``_pending_context`` buffer (``ClaudeCodeAdapter`` et al.), so the
Stage A infrastructure is reused without a second copy.

Opt-in via environment variables read on first singleton access:

- ``DOORAE_CONTEXT_WINDOW_ENABLED=1`` — turn the Stage B rule on
  (default off, preserving Stage A behaviour unchanged).
- ``DOORAE_CONTEXT_WINDOW_SIZE=N`` — advisory window size, used by
  adapters when they cap their buffer. Currently informational; the
  adapter-side ``_PENDING_CONTEXT_MAX`` is the hard cap.

The singleton is an explicit trade-off: Stage B is a deployment-
scoped flag, not a per-call parameter. A single accumulator per
agent process is enough. Tests override it via ``reset_for_tests``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doorae_agent.client import ChatClient


class ContextAccumulator:
    """Decide whether a message qualifies as ambient for capture.

    Does not store anything — the adapter's per-room buffer owns
    storage. This class collapses the Stage B enablement flag and
    the ambient-eligibility rule into one testable object.
    """

    def __init__(self, window_size: int = 10, enabled: bool = False) -> None:
        # Hard lower bound of 1: a zero-sized window is just
        # "disabled" expressed twice.
        self._window_size = max(1, window_size)
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def window_size(self) -> int:
        return self._window_size

    def should_capture(
        self, msg: dict[str, Any], client: "ChatClient"
    ) -> bool:
        """Return True when an unflagged ambient message is worth
        buffering for the next active turn.

        Filter rules (all must pass):
        - accumulator is enabled
        - sender is not this agent (self-echo would double into the
          engine's own session history)
        - content is non-empty after strip (typing indicators and
          membership events carry ``""`` and aren't conversational)
        """
        if not self._enabled:
            return False
        sender = msg.get("participant_id")
        if sender and sender in client._my_participant_ids:
            return False
        content = (msg.get("content") or "").strip()
        if not content:
            return False
        return True


_instance: ContextAccumulator | None = None


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def get_accumulator() -> ContextAccumulator:
    """Return the process-wide accumulator, initialising from env
    on first access.

    The instance is cached so repeated ``decide_policy`` calls
    don't re-read env vars. Tests can force a rebuild via
    ``reset_for_tests``.
    """
    global _instance
    if _instance is None:
        enabled = _parse_bool(os.environ.get("DOORAE_CONTEXT_WINDOW_ENABLED", ""))
        try:
            size = int(os.environ.get("DOORAE_CONTEXT_WINDOW_SIZE", "10"))
        except ValueError:
            size = 10
        _instance = ContextAccumulator(window_size=size, enabled=enabled)
    return _instance


def reset_for_tests() -> None:
    """Drop the cached singleton so the next ``get_accumulator``
    call re-reads env. Pytest fixtures call this between cases so
    env mutations don't leak between tests."""
    global _instance
    _instance = None
