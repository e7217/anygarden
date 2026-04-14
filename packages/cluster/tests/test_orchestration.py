"""Tests for orchestration rules — cooldown, mention parsing, typing tracker."""

from __future__ import annotations

import time

import pytest

from doorae.orchestration.rules import (
    CooldownManager,
    TokenBucket,
    TypingTracker,
    parse_mentions,
)


class TestTokenBucket:
    def test_bucket_allows_within_capacity(self) -> None:
        bucket = TokenBucket(capacity=3, refill_rate=1.0)
        assert bucket.try_consume() is True
        assert bucket.try_consume() is True
        assert bucket.try_consume() is True

    def test_bucket_rejects_when_empty(self) -> None:
        bucket = TokenBucket(capacity=2, refill_rate=0.0)
        assert bucket.try_consume() is True
        assert bucket.try_consume() is True
        assert bucket.try_consume() is False


class TestCooldownManager:
    def test_cooldown_allows_initial_burst(self) -> None:
        mgr = CooldownManager(capacity=3, refill_rate=0.0)
        assert mgr.check_cooldown("p1") is True
        assert mgr.check_cooldown("p1") is True
        assert mgr.check_cooldown("p1") is True
        assert mgr.check_cooldown("p1") is False

    def test_cooldown_separate_per_participant(self) -> None:
        mgr = CooldownManager(capacity=1, refill_rate=0.0)
        assert mgr.check_cooldown("p1") is True
        assert mgr.check_cooldown("p1") is False
        # p2 has its own bucket
        assert mgr.check_cooldown("p2") is True


class TestMentionParsing:
    def test_parse_single_mention(self) -> None:
        result = parse_mentions("Hey @PM check this")
        assert result == [{"type": "legacy", "name": "PM"}]

    def test_parse_multiple_mentions(self) -> None:
        result = parse_mentions("@DevAgent and @QA-Bot please review")
        assert result == [
            {"type": "legacy", "name": "DevAgent"},
            {"type": "legacy", "name": "QA-Bot"},
        ]

    def test_parse_no_mentions(self) -> None:
        # email addresses do not trigger legacy mentions (word-boundary guard)
        result = parse_mentions("No mentions here, just an email user@example.com")
        assert result == []
        mentions = parse_mentions("No mentions in this message")
        assert mentions == []


class TestTypingTracker:
    def test_set_and_get_typing(self) -> None:
        tracker = TypingTracker(ttl_seconds=10.0)
        tracker.set_typing("room1", "p1", True)
        active = tracker.get_typing("room1")
        assert "p1" in active

    def test_clear_typing(self) -> None:
        tracker = TypingTracker(ttl_seconds=10.0)
        tracker.set_typing("room1", "p1", True)
        tracker.set_typing("room1", "p1", False)
        active = tracker.get_typing("room1")
        assert "p1" not in active
