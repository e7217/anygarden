"""Orchestration rules — cooldown token bucket, mention parsing, typing state."""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


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


class GuestRoomAggregateLimiter:
    """Per-room cap on combined guest mentions per time window.

    Design doc §11.7 calls for a room-wide cap so a single invite
    shared with many people cannot fan-out into an LLM-cost spike
    via repeated agent mentions. One shared :class:`TokenBucket` per
    room_id, consumed once for every guest mention event.
    """

    def __init__(self, capacity: int = 20, window_seconds: float = 60.0) -> None:
        # ``capacity`` bursts allowed in ``window_seconds`` on average
        # — translated to a refill rate so the bucket smooths over the
        # whole window rather than clipping at second 0.
        self._capacity = capacity
        self._refill_rate = capacity / window_seconds
        self._buckets: dict[str, TokenBucket] = {}

    def check(self, room_id: str) -> bool:
        """Return ``True`` if the guest mention may proceed in *room_id*.

        ``setdefault`` keeps the lazy-populate atomic in a single
        dict op, so two concurrent requests for the same room won't
        each install a fresh (full) bucket and bypass the cap.
        ``TokenBucket`` itself is not lock-free — under async
        scheduling two coroutines may both pass ``try_consume``
        inside a single event-loop tick, overshooting capacity by at
        most 1. That ±1 slop is inherited from ``CooldownManager``
        and acceptable for a cost-smoothing guard.
        """
        bucket = self._buckets.setdefault(
            room_id,
            TokenBucket(capacity=self._capacity, refill_rate=self._refill_rate),
        )
        return bucket.try_consume()


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


# ── Peer-mention safety net (#279) ───────────────────────────────────

# How deep the agent-to-agent mention chain may go inside a single
# user turn before the server starts stripping peer mentions. ``1``
# means: agent A asks agent B (depth 0 → outbound depth 1); B's
# reply targeting A is depth-2 territory and gets its mentions
# stripped. Tuned for 2026-04 telemetry: most "useful" peer asks
# converge in one hop; depth ≥ 2 is overwhelmingly a runaway loop
# (agents re-asking each other on synthesis prompts).
MAX_PEER_DEPTH: int = 1

# How many peer mentions may be broadcast within one ``user_turn``
# (a turn boundary opens whenever a human/guest sends a message).
# Hit-rate caps catastrophic fan-outs that depth alone misses
# (e.g. depth-1 agent peer-asking eight teammates simultaneously).
MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN: int = 8


def is_peer_mention(
    mention: dict[str, Any],
    *,
    sender_agent_id: str | None,
    agent_participants: dict[str, str],
) -> bool:
    """Return True when *mention* targets a peer agent.

    A "peer agent" is any agent participant that is not the sender.
    User and guest mentions never count, nor do legacy ``@name``
    mentions (we can't resolve a name to a participant_id at the
    server boundary, and the safety-net machinery only meaningfully
    governs ID-based delegation).

    ``agent_participants`` maps ``participant_id -> agent_id`` for
    the room — the caller assembles it once per turn so the helper
    is O(1).
    """
    if mention.get("type") != "user":
        return False
    target_pid = mention.get("id")
    if not isinstance(target_pid, str):
        return False
    target_agent_id = agent_participants.get(target_pid)
    if target_agent_id is None:
        return False  # mention targets a human/guest
    if sender_agent_id is not None and target_agent_id == sender_agent_id:
        return False  # self-mention is not a peer ask
    return True


def compute_outbound_peer_depth(
    incoming_metadata: dict[str, Any] | None,
) -> int:
    """Outbound peer_depth = incoming + 1 (or 0 when undefined).

    Used by the broadcast pipeline to stamp a depth counter on every
    agent message that contains at least one peer mention. Pure
    arithmetic; lifting it out of the handler keeps the safety-net
    logic testable in isolation.
    """
    if not incoming_metadata:
        return 0
    raw = incoming_metadata.get("peer_depth")
    try:
        depth = int(raw)
    except (TypeError, ValueError):
        return 0
    return depth + 1 if depth >= 0 else 0


_PEER_MENTION_TOKEN = re.compile(r"\s*<@user:[^>]+>\s*")


def strip_peer_mentions_from_content(
    content: str,
    *,
    peer_pids: Iterable[str],
) -> str:
    """Remove ``<@user:PID>`` tokens for the supplied peer ids.

    Used when ``peer_depth`` or the per-turn budget would otherwise
    let an agent broadcast another peer ask. Preserves the rest of
    the message verbatim so the user still sees the agent's prose
    answer; only the mention machinery is muted.
    """
    pid_set = set(peer_pids)
    if not pid_set:
        return content

    def _drop(match: re.Match[str]) -> str:
        token = match.group(0).strip()
        # Extract the bare pid — token shape: ``<@user:PID>``
        try:
            pid = token[len("<@user:") : -1]
        except Exception:
            return match.group(0)
        if pid in pid_set:
            # Collapse surrounding whitespace to a single space so
            # the prose stays readable.
            return " "
        return match.group(0)

    return _PEER_MENTION_TOKEN.sub(_drop, content)


class PeerHandoffBudget:
    """Per-room counter that resets on every human/guest message.

    Each call to :meth:`consume` decrements the room's remaining
    quota and returns whether the consumer is still under the cap.
    A :meth:`reset` is invoked when a non-agent sender opens a new
    user turn — that's the only valid restart event, because every
    other message could be the agent that just ran out of quota
    trying to sneak in one more handoff.

    In-memory only: matches the precision of
    :class:`GuestRoomAggregateLimiter` and avoids a Redis dependency
    for what is, in practice, a per-process cap. Single-process
    Anygarden deployments see exact accounting; multi-process would see
    ±1 slop, which is the same slop already accepted upstream.
    """

    def __init__(self, capacity: int = MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN) -> None:
        self._capacity = capacity
        self._remaining: dict[str, int] = {}

    def reset(self, room_id: str) -> None:
        """Restore the room's quota to the configured capacity."""
        self._remaining[room_id] = self._capacity

    def consume(self, room_id: str, count: int = 1) -> bool:
        """Try to consume *count* slots. Returns True if allowed."""
        remaining = self._remaining.get(room_id, self._capacity)
        if remaining < count:
            return False
        self._remaining[room_id] = remaining - count
        return True

    def remaining(self, room_id: str) -> int:
        """Read-only peek used by tests and observability."""
        return self._remaining.get(room_id, self._capacity)


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
