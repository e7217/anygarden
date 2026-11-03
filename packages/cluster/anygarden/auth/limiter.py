"""In-process sliding-window failure limiter (#790, #988).

Enough for the single-process node; a multi-replica deployment needs a
shared store.
"""

from __future__ import annotations

import time


class FailureLimiter:
    """Count recent failures per key; refuse once ``limit`` is reached."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        window_start = now - self._window
        recent = [t for t in self._failures.get(key, []) if t >= window_start]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, key: str) -> int | None:
        """Seconds until another attempt is allowed, or None if allowed now."""
        now = time.monotonic()
        recent = self._recent(key, now)
        if len(recent) < self._limit:
            return None
        return max(1, int(recent[0] + self._window - now) + 1)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        self._failures[key] = [*self._recent(key, now), now]

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
