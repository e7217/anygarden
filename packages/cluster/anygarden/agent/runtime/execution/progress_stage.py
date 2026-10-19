"""Map selected runtime metadata to public, ephemeral typing stages.

#880 adds two normalized progress events a runtime may emit beside its raw
ones, so the room adapter can say *what kind* of work runs and keep a trace
of the turn for the reply:

- ``{"event": "activity", "activity": <ActivityKind>, "phase": "started" |
  "completed", "count"?: int}`` -- a closed kind, never a command, path,
  argument, tool name or output.
- ``{"event": "narration", "text": str}`` -- a sentence the agent wrote to the
  user before more tool work. It is reply text, not engine internals.
"""

import json
from collections import Counter
from dataclasses import dataclass
from typing import Literal, get_args

ProgressStage = Literal["preparing", "using_tool", "writing"]
ActivityKind = Literal[
    "command", "file_change", "file_read", "anygarden_tool", "tool", "web_search", "thinking"
]
ActivityPhase = Literal["started", "completed"]

ACTIVITY_KINDS: frozenset[str] = frozenset(get_args(ActivityKind))
# Trace totals use plural keys; ``thinking`` is shown live but never counted.
TALLY_KEYS: dict[str, str] = {
    "command": "commands",
    "file_change": "file_changes",
    "file_read": "file_reads",
    "anygarden_tool": "anygarden_tools",
    "tool": "tools",
    "web_search": "web_searches",
}
MAX_ACTIVITY_COUNT = 10_000
MAX_NARRATION_CHARS = 2_000
MAX_TRACE_STEPS = 50
MAX_TRACE_BYTES = 16_384


def stage_for_progress(payload: dict) -> ProgressStage | None:
    event = payload.get("event")
    item_type = payload.get("item_type")
    if event in {"agent_start", "turn_start", "turn_end", "turn.started"}:
        return "preparing"
    if event == "message_start" and payload.get("role") == "assistant":
        return "writing"
    if event == "item.started":
        if item_type in {"command_execution", "mcp_tool_call", "file_change"}:
            return "using_tool"
        if item_type == "agent_message":
            return "writing"
    if event == "item.completed" and item_type in {
        "command_execution", "mcp_tool_call", "file_change"
    }:
        return "preparing"
    if event == "activity" and payload.get("activity") in TALLY_KEYS:
        return "using_tool" if payload.get("phase") == "started" else "preparing"
    return None


@dataclass(frozen=True)
class Activity:
    kind: ActivityKind
    phase: ActivityPhase
    count: int = 1


def activity_for_progress(payload: dict) -> Activity | None:
    if payload.get("event") != "activity":
        return None
    kind, phase = payload.get("activity"), payload.get("phase")
    count = payload.get("count", 1)
    if kind not in ACTIVITY_KINDS or phase not in {"started", "completed"}:
        return None
    if type(count) is not int or not 0 < count <= MAX_ACTIVITY_COUNT:
        return None
    return Activity(kind, phase, count)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def narration_for_progress(payload: dict) -> str | None:
    if payload.get("event") != "narration":
        return None
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return clip(text.strip(), MAX_NARRATION_CHARS)


class TraceBuilder:
    """One turn's narration and completed work, in order (#880).

    A step is a narration followed by the work done after it. Work before the
    first narration forms a step without one; consecutive narrations with no
    work between them share a step.
    """

    def __init__(self) -> None:
        self._steps: list[dict] = []
        self._totals: Counter[str] = Counter()
        self._started: Counter[str] = Counter()

    def narration(self, text: str) -> None:
        step = self._steps[-1] if self._steps else None
        if step is not None and not step["activities"]:
            step["narration"] = (
                f"{step['narration']}\n\n{text}" if step.get("narration") else text
            )
            return
        self._steps.append({"narration": text, "activities": Counter()})

    def started(self, kind: ActivityKind) -> int:
        """Record a start; returns its ordinal among this turn's ``kind``."""
        self._started[kind] += 1
        return self._started[kind]

    def completed(self, kind: ActivityKind, count: int = 1) -> None:
        key = TALLY_KEYS.get(kind)
        if key is None:
            return
        if not self._steps:
            self._steps.append({"activities": Counter()})
        self._steps[-1]["activities"][key] += count
        self._totals[key] += count

    def finish(self, final_text: str | None) -> dict | None:
        """Serialize the trace, or ``None`` when the turn did no counted work.

        A narration identical to the final answer was the answer itself (a
        turn that ended on tool work replies with its last message), so it is
        dropped rather than shown twice.
        """
        if not self._totals:
            return None
        steps = [
            {
                **({"narration": step["narration"]} if step.get("narration") else {}),
                **({"activities": dict(step["activities"])} if step["activities"] else {}),
            }
            for step in self._steps
        ]
        last = steps[-1] if steps else None
        if last and final_text and last.get("narration", "").strip() == final_text.strip():
            last.pop("narration")
            if not last:
                steps.pop()
        truncated = max(0, len(steps) - MAX_TRACE_STEPS)
        steps = steps[truncated:]
        trace = {"v": 1, "steps": steps, "totals": dict(self._totals), "truncated": truncated}
        while steps and len(json.dumps(trace, ensure_ascii=False).encode()) > MAX_TRACE_BYTES:
            steps.pop(0)
            trace["truncated"] += 1
        return trace
