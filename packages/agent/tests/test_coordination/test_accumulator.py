"""Tests for the Stage B context-accumulator policy object (#74)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from doorae_agent.coordination.accumulator import (
    ContextAccumulator,
    get_accumulator,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Drop the cached accumulator between tests so env mutations
    in one test don't bleed into the next."""
    reset_for_tests()
    yield
    reset_for_tests()


def _make_client(my_pids: set | None = None) -> MagicMock:
    client = MagicMock()
    client._my_participant_ids = my_pids or {"me-pid"}
    return client


class TestShouldCapture:
    def test_disabled_returns_false(self) -> None:
        """Default construction is disabled — Stage A semantics
        preserved when env var is unset."""
        acc = ContextAccumulator(enabled=False)
        assert (
            acc.should_capture(
                {"participant_id": "other", "content": "hi"}, _make_client()
            )
            is False
        )

    def test_enabled_captures_non_self_message(self) -> None:
        acc = ContextAccumulator(enabled=True)
        assert (
            acc.should_capture(
                {"participant_id": "human", "content": "hi"}, _make_client()
            )
            is True
        )

    def test_self_message_filtered_out(self) -> None:
        """Self-authored messages already live in the engine's own
        session history; re-injecting them via the buffer would
        double-count and drift the model's sense of what 'I said'
        vs 'someone else said'."""
        acc = ContextAccumulator(enabled=True)
        client = _make_client(my_pids={"me-pid"})
        msg = {"participant_id": "me-pid", "content": "self talk"}
        assert acc.should_capture(msg, client) is False

    def test_empty_content_filtered_out(self) -> None:
        """Typing indicators / membership events carry empty
        content; capturing them would waste limited buffer slots."""
        acc = ContextAccumulator(enabled=True)
        client = _make_client()
        assert (
            acc.should_capture(
                {"participant_id": "other", "content": ""}, client
            )
            is False
        )
        assert (
            acc.should_capture(
                {"participant_id": "other", "content": "   "}, client
            )
            is False
        )

    def test_window_size_lower_bound(self) -> None:
        """A zero or negative window size is clamped to 1 — a
        value of 0 reads as 'disabled' and we already express that
        via ``enabled=False``; keeping them disjoint avoids
        ambiguity."""
        assert ContextAccumulator(window_size=0).window_size == 1
        assert ContextAccumulator(window_size=-5).window_size == 1


class TestGetAccumulator:
    def test_env_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DOORAE_CONTEXT_WINDOW_ENABLED", raising=False)
        monkeypatch.delenv("DOORAE_CONTEXT_WINDOW_SIZE", raising=False)
        acc = get_accumulator()
        assert acc.enabled is False
        assert acc.window_size == 10

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1", True),
            ("true", True),
            ("YES", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("", False),
            ("nope", False),
        ],
    )
    def test_env_enable_parsing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
        expected: bool,
    ) -> None:
        """Cover the truthy values documented in the env-var
        surface. Undocumented values (``nope``) must stay off so a
        typo doesn't silently enable ambient capture."""
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", raw)
        assert get_accumulator().enabled is expected

    def test_env_window_size_parse(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", "1")
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_SIZE", "25")
        assert get_accumulator().window_size == 25

    def test_env_window_size_invalid_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Garbage env values must not crash the agent at startup
        — fall back to the default window so the rollout stays
        recoverable without a redeploy."""
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", "1")
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_SIZE", "not-a-number")
        assert get_accumulator().window_size == 10

    def test_singleton_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repeated ``decide_policy`` calls shouldn't re-read env
        every time; the cached instance guarantees that ambient
        policy is stable for the life of the process."""
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", "1")
        first = get_accumulator()
        # Change the env — should have no effect until reset.
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", "0")
        second = get_accumulator()
        assert first is second
        assert second.enabled is True
