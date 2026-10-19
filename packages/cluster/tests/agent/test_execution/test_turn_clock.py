"""#892 — a turn times out on silence, never on total length."""

from __future__ import annotations

import math

from anygarden.agent.runtime.execution.turn_clock import TurnClock


class FakeTime:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def started(activity="command"):
    return {"event": "activity", "activity": activity, "phase": "started"}


def completed(activity="command"):
    return {"event": "activity", "activity": activity, "phase": "completed", "count": 1}


def test_expires_after_idle_without_activity():
    now = FakeTime()
    clock = TurnClock(10, now)
    assert clock.remaining() == 10
    now.t += 9.5
    assert clock.remaining() == 0.5
    now.t += 1
    assert clock.remaining() <= 0


def test_touch_extends_the_window():
    now = FakeTime()
    clock = TurnClock(10, now)
    for _ in range(5):
        now.t += 8
        clock.touch()
    # 40s total elapsed, but never 10s of silence.
    assert clock.remaining() == 10


def test_wrapped_emit_touches_and_forwards():
    now = FakeTime()
    clock = TurnClock(10, now)
    seen = []
    emit = clock.wrap(lambda kind, payload: seen.append((kind, payload)))
    now.t += 9
    emit("progress", {"event": "turn.started"})
    assert seen == [("progress", {"event": "turn.started"})]
    assert clock.remaining() == 10


def test_open_tool_suspends_idle_until_completed():
    now = FakeTime()
    clock = TurnClock(10, now)
    emit = clock.wrap(lambda *_: None)
    emit("progress", started())
    now.t += 1000
    assert clock.remaining() == math.inf
    assert clock.open_tools == 1
    emit("progress", completed())
    assert clock.open_tools == 0
    assert clock.remaining() == 10


def test_nested_tools_count_and_thinking_is_not_a_tool():
    now = FakeTime()
    clock = TurnClock(10, now)
    emit = clock.wrap(lambda *_: None)
    emit("progress", started("thinking"))
    assert clock.open_tools == 0
    emit("progress", started())
    emit("progress", started("web_search"))
    emit("progress", completed())
    assert clock.remaining() == math.inf
    emit("progress", completed("web_search"))
    emit("progress", completed("thinking"))
    assert clock.open_tools == 0
    now.t += 11
    assert clock.remaining() <= 0


def test_extra_completions_clamp_at_zero():
    now = FakeTime()
    clock = TurnClock(10, now)
    emit = clock.wrap(lambda *_: None)
    emit("progress", completed())
    emit("progress", completed())
    assert clock.open_tools == 0
    emit("progress", started())
    assert clock.open_tools == 1


def test_tool_ended_closes_a_tool_without_an_emit():
    # Collectors drop the "completed" emit of a failed edit (#880); the
    # tool still ended and must not pin the turn open forever.
    now = FakeTime()
    clock = TurnClock(10, now)
    emit = clock.wrap(lambda *_: None)
    emit("progress", started("file_change"))
    clock.tool_ended()
    assert clock.open_tools == 0
    clock.tool_ended()
    assert clock.open_tools == 0


def test_non_progress_and_odd_payloads_only_touch():
    now = FakeTime()
    clock = TurnClock(10, now)
    emit = clock.wrap(lambda *_: None)
    now.t += 5
    emit("progress", "not-a-dict")  # type: ignore[arg-type]
    emit("other", started())
    assert clock.open_tools == 0
    assert clock.remaining() == 10
