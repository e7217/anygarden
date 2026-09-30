"""CrashBudget — per-agent crash rate limiter using a sliding window."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class CrashBudget:
    """Tracks crash frequency for a single agent using a sliding window.

    Records crash timestamps and allows restarts up to ``max_restarts``
    within the most recent ``window_seconds``.  Timestamps older than
    ``window_seconds`` are pruned on every access so the window slides
    automatically.

    Uses ``time.monotonic()`` to avoid issues with system clock adjustments.
    """

    max_restarts: int = 3
    window_seconds: int = 300
    _timestamps: list[float] = field(default_factory=list)

    def _prune(self) -> None:
        """Remove timestamps that have fallen outside the current window."""
        cutoff = time.monotonic() - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]

    def record_crash(self) -> bool:
        """Record a crash and determine whether a restart is permitted.

        Prunes expired timestamps first, then appends the current time.

        Returns:
            True  — restart is allowed (crash count <= max_restarts).
            False — crash budget exhausted; do not restart.
        """
        self._prune()
        self._timestamps.append(time.monotonic())
        return len(self._timestamps) <= self.max_restarts

    def reset(self) -> None:
        """Clear all recorded crash timestamps, resetting the budget."""
        self._timestamps.clear()

    @property
    def crash_count(self) -> int:
        """Number of crashes recorded within the current sliding window."""
        self._prune()
        return len(self._timestamps)
