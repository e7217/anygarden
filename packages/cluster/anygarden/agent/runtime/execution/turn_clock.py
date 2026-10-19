"""Idle clock for one engine turn (#892).

A turn ends on silence, not on total length: every stdout line and progress
emit counts as activity. While a tool the engine reported as started has not
completed, the turn is never idle -- a long silent command (a remote disk
clone over ssh) is normal work; a stuck one ends with the user's Stop.
"""

from __future__ import annotations

import math
from collections.abc import Callable

Emit = Callable[[str, dict], None]


class TurnClock:
    def __init__(self, idle: float, now: Callable[[], float]):
        self.idle = idle
        self._now = now
        self.started = now()
        self.last_touch = self.started
        self.open_tools = 0

    def touch(self) -> None:
        self.last_touch = self._now()

    def tool_ended(self) -> None:
        """Close a tool whose completion the collector does not emit."""
        self.open_tools = max(0, self.open_tools - 1)

    def observe(self, kind: str, payload: object) -> None:
        self.touch()
        if kind != "progress" or not isinstance(payload, dict):
            return
        if payload.get("event") != "activity" or payload.get("activity") == "thinking":
            return
        phase = payload.get("phase")
        if phase == "started":
            self.open_tools += 1
        elif phase == "completed":
            self.tool_ended()

    def wrap(self, emit: Emit) -> Emit:
        def wrapped(kind: str, payload: dict) -> None:
            self.observe(kind, payload)
            emit(kind, payload)

        return wrapped

    def remaining(self) -> float:
        if self.open_tools > 0:
            return math.inf
        return self.last_touch + self.idle - self._now()

    def elapsed(self) -> float:
        return self._now() - self.started
