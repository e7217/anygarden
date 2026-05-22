"""Unit tests for cycle_guard — semantic loop detection (#157 Phase B)."""

from __future__ import annotations

from collections import deque

import pytest

from anygarden_agent.integrations.cycle_guard import (
    hash_content,
    is_cycle_detected,
)


class TestHashContent:
    """``hash_content`` returns a 16-char hex for content ≥16 chars,
    ``None`` otherwise. Short content is excluded to keep 'ok' / '네'
    style repeats out of the cycle counter."""

    def test_long_content_returns_hash(self) -> None:
        h = hash_content("this is a full sentence that definitely exceeds 16 chars")
        assert h is not None
        assert len(h) == 16

    def test_short_content_returns_none(self) -> None:
        assert hash_content("ok") is None
        assert hash_content("네 알겠어요") is None
        assert hash_content("x" * 15) is None

    def test_boundary_at_16_chars(self) -> None:
        assert hash_content("x" * 16) is not None

    def test_case_insensitive_hash(self) -> None:
        """Casefold before hashing so 'Hello' and 'hello' collide."""
        a = hash_content("Hello world — same content please")
        b = hash_content("HELLO WORLD — same content please")
        assert a == b

    def test_only_first_64_chars_hashed(self) -> None:
        """Prefix-based hash: differing suffixes past 64 chars collide."""
        base = "a" * 64
        assert hash_content(base + "suffix-one") == hash_content(base + "suffix-two")

    def test_different_content_differs(self) -> None:
        a = hash_content("this content is definitely not the other")
        b = hash_content("completely different sentence here OK")
        assert a != b

    def test_empty_content_returns_none(self) -> None:
        assert hash_content("") is None


class TestIsCycleDetected:
    """``is_cycle_detected`` flags a (sender, hash) pair as looping
    when it appears ≥ ``min_repetitions`` times in the last
    ``window`` entries of ``recent``."""

    def _msg(self, sender: str, content: str) -> dict:
        return {"participant_id": sender, "content": content}

    def _entry(self, sender: str, content: str) -> dict:
        return {"sender": sender, "hash": hash_content(content)}

    def test_no_recent_history_no_cycle(self) -> None:
        msg = self._msg("A", "this is a long enough message")
        assert is_cycle_detected(msg, []) is False

    def test_single_prior_match_no_cycle(self) -> None:
        """Default ``min_repetitions=2`` — one prior occurrence is not a loop."""
        content = "the same message said again"
        msg = self._msg("A", content)
        recent = [self._entry("A", content)]
        assert is_cycle_detected(msg, recent) is False

    def test_two_prior_matches_is_cycle(self) -> None:
        """Two prior occurrences from the same sender trips the guard."""
        content = "the exact same message said again"
        msg = self._msg("A", content)
        recent = [
            self._entry("A", content),
            self._entry("A", content),
        ]
        assert is_cycle_detected(msg, recent) is True

    def test_different_senders_dont_collide(self) -> None:
        """Same content from a different sender is not this agent's loop."""
        content = "that exact same message again please"
        msg = self._msg("A", content)
        recent = [
            self._entry("B", content),
            self._entry("C", content),
        ]
        assert is_cycle_detected(msg, recent) is False

    def test_short_content_never_loops(self) -> None:
        """Messages < 16 chars have hash=None and are excluded."""
        msg = self._msg("A", "ok")
        # Even with many prior "ok" messages the guard stays silent
        recent = [{"sender": "A", "hash": None} for _ in range(5)]
        assert is_cycle_detected(msg, recent) is False

    def test_window_limits_lookback(self) -> None:
        """Default window=6 — matches beyond that don't count."""
        content = "look at this repeated content once again"
        msg = self._msg("A", content)
        # Matches from 10 slots ago + 5 unrelated in between
        recent = [
            self._entry("A", content),  # slot 0
            self._entry("A", content),  # slot 1
            self._entry("X", "unrelated entry one that's distinct"),
            self._entry("X", "unrelated entry two that's distinct"),
            self._entry("X", "unrelated entry three that's distinct"),
            self._entry("X", "unrelated entry four that's distinct"),
            self._entry("X", "unrelated entry five that's distinct"),
            self._entry("X", "unrelated entry six that's distinct"),
        ]
        # Last 6 entries contain 0 matches
        assert is_cycle_detected(msg, recent, window=6) is False

    def test_window_covers_matches(self) -> None:
        """Matches within the window are counted."""
        content = "repeated matching content in scope"
        msg = self._msg("A", content)
        recent = [
            self._entry("X", "something else long enough right"),
            self._entry("A", content),  # inside window-6 of tail
            self._entry("A", content),
            self._entry("X", "something long enough sure thing"),
            self._entry("X", "another long enough right yes OK"),
        ]
        assert is_cycle_detected(msg, recent, window=6) is True

    def test_custom_min_repetitions(self) -> None:
        content = "same sentence repeated for a test"
        msg = self._msg("A", content)
        recent = [self._entry("A", content)] * 2
        # With min_repetitions=3 → False (only 2 matches)
        assert is_cycle_detected(msg, recent, min_repetitions=3) is False
        # With min_repetitions=2 → True
        assert is_cycle_detected(msg, recent, min_repetitions=2) is True

    def test_deque_iterable_supported(self) -> None:
        """Real client code passes a ``collections.deque``."""
        content = "deque-backed recent history sample"
        msg = self._msg("A", content)
        recent: deque[dict] = deque(maxlen=10)
        recent.append(self._entry("A", content))
        recent.append(self._entry("A", content))
        assert is_cycle_detected(msg, recent) is True
