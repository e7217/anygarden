"""Tests for orchestration rules — cooldown, mention parsing, typing tracker."""

from __future__ import annotations

import time

import pytest

from doorae.orchestration.rules import (
    CooldownManager,
    PeerHandoffBudget,
    TokenBucket,
    TypingTracker,
    compute_outbound_peer_depth,
    is_peer_mention,
    parse_mentions,
    strip_peer_mentions_from_content,
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


class TestPeerMentionSafetyNet:
    """Issue #279 — depth, kind, and budget helpers for the
    collaborative agent peer-mention pipeline."""

    def test_is_peer_mention_targeting_other_agent(self) -> None:
        agents = {"pid-a": "agent-a", "pid-b": "agent-b"}
        mention = {"type": "user", "id": "pid-b"}
        assert (
            is_peer_mention(
                mention, sender_agent_id="agent-a", agent_participants=agents
            )
            is True
        )

    def test_is_peer_mention_self_returns_false(self) -> None:
        agents = {"pid-a": "agent-a"}
        mention = {"type": "user", "id": "pid-a"}
        assert (
            is_peer_mention(
                mention, sender_agent_id="agent-a", agent_participants=agents
            )
            is False
        )

    def test_is_peer_mention_human_target_returns_false(self) -> None:
        # ``pid-h`` is a human participant (not in agent_participants).
        agents = {"pid-a": "agent-a"}
        mention = {"type": "user", "id": "pid-h"}
        assert (
            is_peer_mention(
                mention, sender_agent_id="agent-a", agent_participants=agents
            )
            is False
        )

    def test_is_peer_mention_legacy_type_rejected(self) -> None:
        # Legacy @name mentions can't be resolved server-side; only
        # ID-based mentions feed the safety net.
        assert (
            is_peer_mention(
                {"type": "legacy", "name": "agent-b"},
                sender_agent_id="agent-a",
                agent_participants={},
            )
            is False
        )

    def test_compute_outbound_peer_depth_default(self) -> None:
        assert compute_outbound_peer_depth(None) == 0
        assert compute_outbound_peer_depth({}) == 0
        assert compute_outbound_peer_depth({"peer_depth": "garbage"}) == 0

    def test_compute_outbound_peer_depth_increments(self) -> None:
        assert compute_outbound_peer_depth({"peer_depth": 0}) == 1
        assert compute_outbound_peer_depth({"peer_depth": 1}) == 2

    def test_strip_peer_mentions_removes_only_listed_pids(self) -> None:
        content = "Need help <@user:peer-a> and <@user:peer-b>?"
        out = strip_peer_mentions_from_content(content, peer_pids={"peer-a"})
        assert "<@user:peer-a>" not in out
        # peer-b stays.
        assert "<@user:peer-b>" in out

    def test_strip_peer_mentions_collapses_whitespace(self) -> None:
        content = "Hello <@user:peer-a> world"
        out = strip_peer_mentions_from_content(
            content, peer_pids={"peer-a"}
        )
        assert "<@user:peer-a>" not in out
        # No double-spaces left from token removal.
        assert "  " not in out
        assert "Hello" in out and "world" in out

    def test_strip_peer_mentions_empty_pids_returns_unchanged(self) -> None:
        content = "Plain content with <@user:p1>"
        assert strip_peer_mentions_from_content(content, peer_pids=set()) == content

    def test_peer_handoff_budget_consume_then_block(self) -> None:
        budget = PeerHandoffBudget(capacity=2)
        assert budget.consume("room-1") is True  # 1 remaining
        assert budget.consume("room-1") is True  # 0 remaining
        assert budget.consume("room-1") is False
        # Other rooms keep their full quota.
        assert budget.consume("room-2") is True

    def test_peer_handoff_budget_reset_restores_capacity(self) -> None:
        budget = PeerHandoffBudget(capacity=1)
        assert budget.consume("room-1") is True
        assert budget.consume("room-1") is False
        budget.reset("room-1")
        assert budget.remaining("room-1") == 1
        assert budget.consume("room-1") is True

    def test_peer_handoff_budget_consume_count_more_than_remaining(self) -> None:
        budget = PeerHandoffBudget(capacity=3)
        assert budget.consume("room-1", count=4) is False
        # Failed consume must not partially deplete — atomic on caller side.
        assert budget.remaining("room-1") == 3
