"""#880 — the closed shape of an agent reply's ``execution_trace`` metadata."""

from __future__ import annotations

import pytest

from anygarden.agent.runtime.execution.progress_stage import (
    MAX_NARRATION_CHARS,
    MAX_TRACE_STEPS,
    TALLY_KEYS,
)
from anygarden.messages.metadata import (
    EXECUTION_TRACE_KEY,
    TRACE_TALLY_KEYS,
    sanitize_execution_trace,
)

GOOD = {
    "v": 1,
    "steps": [
        {"activities": {"commands": 1}},
        {"narration": "Deploying now.", "activities": {"commands": 2, "file_changes": 1}},
    ],
    "totals": {"commands": 3, "file_changes": 1},
    "truncated": 2,
}


def test_server_and_agent_agree_on_tally_keys():
    assert TRACE_TALLY_KEYS == frozenset(TALLY_KEYS.values())


@pytest.mark.req("ENG-12")
def test_only_agents_may_carry_a_trace():
    metadata = {EXECUTION_TRACE_KEY: GOOD, "note": 1}
    assert sanitize_execution_trace(metadata, sender_is_agent=False) == {"note": 1}
    assert sanitize_execution_trace(metadata, sender_is_agent=True) == metadata


@pytest.mark.parametrize("trace", [
    None, "x", [], {"v": 2, "steps": [], "totals": {"commands": 1}},
    {"v": 1, "steps": [], "totals": {}},
    {"v": 1, "steps": [], "totals": {"commands": 0}},
    {"v": 1, "steps": "x", "totals": {"commands": 1}},
])
def test_unusable_trace_is_removed(trace):
    assert sanitize_execution_trace({EXECUTION_TRACE_KEY: trace}, sender_is_agent=True) == {}


def test_trace_is_normalized_to_the_closed_shape():
    trace = {
        "v": 1,
        "steps": [
            {"narration": "ok", "activities": {"commands": 1, "secrets": 5, "tools": True}},
            {"narration": "x" * (MAX_NARRATION_CHARS + 10)},
            {"activities": {"tools": 10_001}},
            {},
            "junk",
        ],
        "totals": {"commands": 1, "evil": 1},
        "truncated": -4,
        "raw": "DO-NOT-PUBLISH",
    }
    clean = sanitize_execution_trace({EXECUTION_TRACE_KEY: trace}, sender_is_agent=True)
    assert clean[EXECUTION_TRACE_KEY] == {
        "v": 1,
        "steps": [
            {"narration": "ok", "activities": {"commands": 1}},
            {"narration": "x" * (MAX_NARRATION_CHARS - 1) + "…"},
        ],
        "totals": {"commands": 1},
        "truncated": 0,
    }


def test_too_many_steps_keep_the_latest():
    trace = {
        "v": 1,
        "steps": [{"narration": f"step {i}"} for i in range(MAX_TRACE_STEPS + 3)],
        "totals": {"commands": 1},
        "truncated": 1,
    }
    result = sanitize_execution_trace({EXECUTION_TRACE_KEY: trace}, sender_is_agent=True)[
        EXECUTION_TRACE_KEY
    ]
    assert len(result["steps"]) == MAX_TRACE_STEPS
    assert result["steps"][0] == {"narration": "step 3"}
    assert result["truncated"] == 4
