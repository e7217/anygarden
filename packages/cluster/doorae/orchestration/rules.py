"""Orchestration rules — cooldown token bucket, mention parsing, typing state."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


# ── Cooldown Token Bucket ────────────────────────────────────────────


@dataclass
class TokenBucket:
    """Simple token-bucket rate limiter for per-participant cooldown.

    - *capacity*: maximum burst size
    - *refill_rate*: tokens added per second
    """

    capacity: int = 5
    refill_rate: float = 1.0  # tokens per second
    _tokens: float = field(init=False, default=0.0)
    _last_refill: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()

    def try_consume(self, tokens: int = 1) -> bool:
        """Attempt to consume *tokens*.  Returns True if allowed."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now


class CooldownManager:
    """Manages per-participant cooldown buckets."""

    def __init__(self, capacity: int = 5, refill_rate: float = 1.0) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._buckets: dict[str, TokenBucket] = {}

    def check_cooldown(self, participant_id: str) -> bool:
        """Return True if the participant is allowed to send, False if rate-limited."""
        if participant_id not in self._buckets:
            self._buckets[participant_id] = TokenBucket(
                capacity=self._capacity,
                refill_rate=self._refill_rate,
            )
        return self._buckets[participant_id].try_consume()


# ── Mention Parsing ──────────────────────────────────────────────────

# ID-based mention tokens: <@user:id> and <#room:id>
_ID_MENTION_PATTERN = re.compile(r"<@user:([^>]+)>|<#room:([^>]+)>")
# Legacy @Name mentions (backward compat)
_LEGACY_MENTION_PATTERN = re.compile(r"(?<!\w)@([\w-]+)")


def parse_mentions(content: str) -> list[dict[str, str]]:
    """Extract mentions from a message.

    Supports two formats:
    - ID-based: ``<@user:abc123>`` → ``{"type": "user", "id": "abc123"}``
    - ID-based: ``<#room:xyz789>`` → ``{"type": "room", "id": "xyz789"}``
    - Legacy:   ``@Name``          → ``{"type": "legacy", "name": "Name"}``

    >>> parse_mentions("<@user:abc> and <#room:xyz>")
    [{'type': 'user', 'id': 'abc'}, {'type': 'room', 'id': 'xyz'}]
    >>> parse_mentions("Hey @Alice")
    [{'type': 'legacy', 'name': 'Alice'}]
    """
    mentions: list[dict[str, str]] = []
    for m in _ID_MENTION_PATTERN.finditer(content):
        if m.group(1):
            mentions.append({"type": "user", "id": m.group(1)})
        elif m.group(2):
            mentions.append({"type": "room", "id": m.group(2)})
    # Only fall back to legacy parsing when no ID-based mentions found
    if not mentions:
        for m in _LEGACY_MENTION_PATTERN.finditer(content):
            mentions.append({"type": "legacy", "name": m.group(1)})
    return mentions


# ── Typing State ─────────────────────────────────────────────────────


class TypingTracker:
    """Tracks who is currently typing in each room.

    Each typing event has a TTL — if not refreshed, it expires.
    """

    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._ttl = ttl_seconds
        # room_id -> { participant_id -> last_typing_timestamp }
        self._state: dict[str, dict[str, float]] = {}

    def set_typing(self, room_id: str, participant_id: str, is_typing: bool) -> None:
        """Record typing state for a participant."""
        room_state = self._state.setdefault(room_id, {})
        if is_typing:
            room_state[participant_id] = time.monotonic()
        else:
            room_state.pop(participant_id, None)

    def get_typing(self, room_id: str) -> list[str]:
        """Return list of participant IDs currently typing in *room_id*."""
        room_state = self._state.get(room_id, {})
        now = time.monotonic()
        active: list[str] = []
        expired: list[str] = []
        for pid, ts in room_state.items():
            if now - ts <= self._ttl:
                active.append(pid)
            else:
                expired.append(pid)
        # Clean up expired entries
        for pid in expired:
            room_state.pop(pid, None)
        return active
