"""Semantic cycle detection for ``decide_policy`` (#157 Phase B).

Issue #67 / #157 Phase A added a counter-based brake on agent-only
loops: ``max_agent_turns`` caps consecutive agent messages and a task-
init reset guard prevents prefix abuse. Neither catches the pattern
where two agents exchange the *same* content over and over — the
$47K production loop cited in the 2026-04-19 deep-research report is
exactly that shape.

This module provides a content-hash based detector. ``decide_policy``
consults it right before deciding whether to RESPOND; when the
(sender, hash) pair of the incoming message has already appeared
``min_repetitions`` times in the room's recent history, the agent
drops the message and breaks the cycle.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

# Content shorter than this is excluded from hashing — short replies
# ("ok", "네", "done") legitimately repeat and must not trip the guard.
_MIN_HASHABLE_LEN = 16

# Only the first N characters are fed to the hash. Keeps the detector
# robust against trailing salt (timestamps, ids) added by agents that
# otherwise produce the same response body.
_HASH_PREFIX_LEN = 64

# Hex digest truncation — 16 hex chars = 64 bits of entropy, plenty
# for room-scoped cycle detection where the window is a handful of
# messages.
_HASH_OUTPUT_LEN = 16


def hash_content(content: str) -> str | None:
    """Stable 16-hex-char hash of the first 64 chars of ``content``.

    Returns None when ``content`` is shorter than :data:`_MIN_HASHABLE_LEN`
    so short legitimate repeats never feed the detector.
    """
    if len(content) < _MIN_HASHABLE_LEN:
        return None
    prefix = content[:_HASH_PREFIX_LEN].casefold()
    return hashlib.sha1(prefix.encode("utf-8")).hexdigest()[:_HASH_OUTPUT_LEN]


def is_cycle_detected(
    msg: dict[str, Any],
    recent: Iterable[dict[str, Any]],
    *,
    window: int = 6,
    min_repetitions: int = 2,
) -> bool:
    """Return True when ``msg``'s (sender, hash) pair loops in ``recent``.

    ``recent`` is an iterable of prior observations, each a mapping with
    ``sender`` (participant_id) and ``hash`` (from :func:`hash_content`).
    Only the last ``window`` entries are inspected. The guard fires when
    the pair appears at least ``min_repetitions`` times in that slice —
    i.e. the exact same sender has already said the exact same thing
    that many times recently.

    Returns False for short content (hash is None) or missing sender,
    so callers can always run the guard unconditionally.
    """
    sender = msg.get("participant_id")
    content = msg.get("content", "")
    msg_hash = hash_content(content)
    if not sender or msg_hash is None:
        return False

    recent_list = list(recent)[-window:]
    hits = sum(
        1
        for entry in recent_list
        if entry.get("sender") == sender and entry.get("hash") == msg_hash
    )
    return hits >= min_repetitions
