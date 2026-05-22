"""Tests for CrashBudget — per-agent crash rate limiter."""

from __future__ import annotations

import time

import pytest

from anygarden_machine.crash_budget import CrashBudget


class TestCrashBudgetFirstCrash:
    """First crash should always allow restart."""

    def test_first_crash_allows_restart(self) -> None:
        budget = CrashBudget()
        result = budget.record_crash()
        assert result is True


class TestCrashBudgetWithinBudget:
    """Crashes within max_restarts limit should all allow restart."""

    def test_three_crashes_with_max_three_all_allow_restart(self) -> None:
        budget = CrashBudget(max_restarts=3)
        results = [budget.record_crash() for _ in range(3)]
        assert all(results), f"Expected all True, got {results}"

    def test_one_crash_with_max_one_allows_restart(self) -> None:
        budget = CrashBudget(max_restarts=1)
        assert budget.record_crash() is True

    def test_two_crashes_with_max_five_both_allow_restart(self) -> None:
        budget = CrashBudget(max_restarts=5)
        assert budget.record_crash() is True
        assert budget.record_crash() is True


class TestCrashBudgetExceeded:
    """4th crash with max_restarts=3 should deny restart."""

    def test_fourth_crash_with_max_three_denies_restart(self) -> None:
        budget = CrashBudget(max_restarts=3)
        budget.record_crash()
        budget.record_crash()
        budget.record_crash()
        # 4th crash exceeds budget
        result = budget.record_crash()
        assert result is False

    def test_exceeded_budget_continues_to_deny(self) -> None:
        budget = CrashBudget(max_restarts=2)
        budget.record_crash()
        budget.record_crash()
        # 3rd and 4th both denied
        assert budget.record_crash() is False
        assert budget.record_crash() is False


class TestCrashBudgetExpiredPruning:
    """Expired timestamps outside the window should be pruned."""

    def test_backdated_timestamps_pruned_on_next_crash(self) -> None:
        budget = CrashBudget(max_restarts=3, window_seconds=1)
        # Fill the budget
        budget.record_crash()
        budget.record_crash()
        budget.record_crash()
        # Manually backdate all timestamps to outside the 1s window
        budget._timestamps = [t - 2.0 for t in budget._timestamps]
        # Next crash should succeed because old ones are pruned
        result = budget.record_crash()
        assert result is True

    def test_crash_count_excludes_expired_timestamps(self) -> None:
        budget = CrashBudget(max_restarts=3, window_seconds=1)
        budget.record_crash()
        budget.record_crash()
        # Backdate so they expire
        budget._timestamps = [t - 5.0 for t in budget._timestamps]
        # crash_count should return 0 after pruning
        assert budget.crash_count == 0

    def test_mixed_expired_and_active_timestamps(self) -> None:
        budget = CrashBudget(max_restarts=5, window_seconds=60)
        budget.record_crash()
        budget.record_crash()
        # Backdate first crash beyond window
        budget._timestamps[0] -= 120.0
        # Only 1 active crash remains
        assert budget.crash_count == 1


class TestCrashBudgetReset:
    """reset() should clear all crash history."""

    def test_reset_clears_all_timestamps(self) -> None:
        budget = CrashBudget(max_restarts=2)
        budget.record_crash()
        budget.record_crash()
        budget.reset()
        assert budget.crash_count == 0

    def test_after_reset_budget_allows_restart_again(self) -> None:
        budget = CrashBudget(max_restarts=2)
        budget.record_crash()
        budget.record_crash()
        budget.record_crash()  # denied
        budget.reset()
        # Should allow restart after reset
        assert budget.record_crash() is True

    def test_reset_on_empty_budget_is_idempotent(self) -> None:
        budget = CrashBudget()
        budget.reset()
        budget.reset()
        assert budget.crash_count == 0


class TestCrashBudgetCrashCount:
    """crash_count property returns number of active crashes in window."""

    def test_initial_crash_count_is_zero(self) -> None:
        budget = CrashBudget()
        assert budget.crash_count == 0

    def test_crash_count_increments_with_each_crash(self) -> None:
        budget = CrashBudget()
        assert budget.crash_count == 0
        budget.record_crash()
        assert budget.crash_count == 1
        budget.record_crash()
        assert budget.crash_count == 2

    def test_crash_count_reflects_active_window_only(self) -> None:
        budget = CrashBudget(max_restarts=10, window_seconds=60)
        budget.record_crash()
        budget.record_crash()
        budget.record_crash()
        assert budget.crash_count == 3

    def test_crash_count_uses_monotonic_time(self) -> None:
        """Ensure timestamps use time.monotonic() (not time.time()) — indirect verification."""
        budget = CrashBudget(max_restarts=5, window_seconds=300)
        before = time.monotonic()
        budget.record_crash()
        after = time.monotonic()
        # The stored timestamp should be within [before, after]
        assert len(budget._timestamps) == 1
        assert before <= budget._timestamps[0] <= after
