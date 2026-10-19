from unittest.mock import AsyncMock

import pytest

from anygarden.agent.integrations.room_execution import RoomExecutionAdapter
from anygarden.agent.runtime.execution.progress_stage import (
    MAX_NARRATION_CHARS,
    MAX_TRACE_STEPS,
    TraceBuilder,
    activity_for_progress,
    narration_for_progress,
    stage_for_progress,
)


@pytest.mark.parametrize("payload,expected", [
    ({"event": "turn.started"}, "preparing"),
    ({"event": "item.started", "item_type": "command_execution", "command": "SECRET"}, "using_tool"),
    ({"event": "item.started", "item_type": "mcp_tool_call"}, "using_tool"),
    ({"event": "item.started", "item_type": "file_change"}, "using_tool"),
    ({"event": "item.completed", "item_type": "command_execution"}, "preparing"),
    ({"event": "item.started", "item_type": "agent_message"}, "writing"),
    ({"event": "message_start", "role": "assistant"}, "writing"),
    ({"event": "message_start", "role": "user"}, None),
    ({"event": "message_start"}, None),
    ({"event": "item.updated", "item_type": "command_execution"}, None),
    ({"event": "item.started", "item_type": "untrusted"}, None),
])
def test_progress_stage_allowlist(payload, expected):
    assert stage_for_progress(payload) == expected


@pytest.mark.asyncio
async def test_progress_stage_fences_executions_and_suppresses_repeats():
    adapter = RoomExecutionAdapter()
    adapter._client = AsyncMock()
    adapter._active_execution_by_room['room'] = 'new'
    adapter._active_execution_by_room['other'] = 'other-id'
    await adapter._set_progress("room", "new", "preparing")
    await adapter._set_progress("room", "new", "preparing")
    await adapter._set_progress("room", "old", "using_tool")
    await adapter._set_progress("other", "other-id", "writing")
    assert adapter.progress_stage("room") == "preparing"
    assert adapter.progress_stage("other") == "writing"
    assert adapter._client.sendTyping.call_count == 2
    adapter._client.sendTyping.assert_any_await("room", True, "preparing")
    adapter._active_execution_by_room.pop('room')
    await adapter._set_progress('room', 'new', 'writing')
    assert adapter.progress_stage('room') == 'preparing'


# #880 — normalized activity / narration progress and the per-turn work trace.


@pytest.mark.req("ENG-10")
@pytest.mark.parametrize("payload,expected", [
    ({"event": "activity", "activity": "command", "phase": "started"}, ("command", "started", 1)),
    ({"event": "activity", "activity": "file_change", "phase": "completed", "count": 3},
     ("file_change", "completed", 3)),
    ({"event": "activity", "activity": "thinking", "phase": "started"}, ("thinking", "started", 1)),
    ({"event": "activity", "activity": "secret", "phase": "started"}, None),
    ({"event": "activity", "activity": "command", "phase": "maybe"}, None),
    ({"event": "activity", "activity": "command", "phase": "completed", "count": 0}, None),
    ({"event": "activity", "activity": "command", "phase": "completed", "count": True}, None),
    ({"event": "item.started", "item_type": "command_execution"}, None),
])
def test_activity_for_progress_is_a_closed_mapping(payload, expected):
    activity = activity_for_progress(payload)
    if expected is None:
        assert activity is None
    else:
        assert (activity.kind, activity.phase, activity.count) == expected


@pytest.mark.parametrize("payload,expected", [
    ({"event": "activity", "activity": "command", "phase": "started"}, "using_tool"),
    ({"event": "activity", "activity": "command", "phase": "completed"}, "preparing"),
    ({"event": "activity", "activity": "thinking", "phase": "started"}, None),
    ({"event": "narration", "text": "Next I will check the logs."}, None),
])
def test_normalized_progress_keeps_public_stages(payload, expected):
    assert stage_for_progress(payload) == expected


def test_narration_for_progress_accepts_only_bounded_text():
    assert narration_for_progress({"event": "narration", "text": "  Checking.  "}) == "Checking."
    assert narration_for_progress({"event": "narration", "text": "   "}) is None
    assert narration_for_progress({"event": "narration", "text": 3}) is None
    assert narration_for_progress({"event": "activity", "text": "x"}) is None
    long = narration_for_progress({"event": "narration", "text": "a" * (MAX_NARRATION_CHARS + 50)})
    assert len(long) == MAX_NARRATION_CHARS and long.endswith("…")


@pytest.mark.req("ENG-11")
def test_trace_groups_activities_under_the_narration_before_them():
    trace = TraceBuilder()
    assert trace.started("command") == 1
    trace.completed("command")
    trace.narration("There are 4 new commits. Same procedure as before.")
    assert trace.started("command") == 2
    trace.completed("command")
    trace.narration("Built both nodes.")
    trace.narration("Restarting and checking.")
    trace.started("file_change")
    trace.completed("file_change", 2)
    trace.started("command")
    trace.completed("command")
    assert trace.finish("Both nodes are on 7e54fa7.") == {
        "v": 1,
        "steps": [
            {"activities": {"commands": 1}},
            {"narration": "There are 4 new commits. Same procedure as before.",
             "activities": {"commands": 1}},
            {"narration": "Built both nodes.\n\nRestarting and checking.",
             "activities": {"file_changes": 2, "commands": 1}},
        ],
        "totals": {"commands": 3, "file_changes": 2},
        "truncated": 0,
    }


def test_trace_counts_only_completed_work_and_never_counts_thinking():
    trace = TraceBuilder()
    trace.started("thinking")
    trace.completed("thinking")
    trace.started("command")  # cancelled before completion
    assert trace.finish("answer") is None


def test_trace_drops_the_narration_that_became_the_final_answer():
    trace = TraceBuilder()
    trace.narration("Checking the repo.")
    trace.started("command")
    trace.completed("command")
    trace.narration("The repo has 1 commit.")
    assert trace.finish("The repo has 1 commit.") == {
        "v": 1,
        "steps": [{"narration": "Checking the repo.", "activities": {"commands": 1}}],
        "totals": {"commands": 1},
        "truncated": 0,
    }


def test_trace_without_tool_work_is_none():
    trace = TraceBuilder()
    trace.narration("Let me think.")
    assert trace.finish("The answer.") is None


def test_trace_keeps_the_latest_steps_and_full_totals():
    trace = TraceBuilder()
    for index in range(MAX_TRACE_STEPS + 5):
        trace.narration(f"step {index}")
        trace.started("command")
        trace.completed("command")
    result = trace.finish("done")
    assert len(result["steps"]) == MAX_TRACE_STEPS
    assert result["steps"][0]["narration"] == "step 5"
    assert result["truncated"] == 5
    assert result["totals"] == {"commands": MAX_TRACE_STEPS + 5}


def test_trace_stays_under_the_byte_budget():
    import json

    trace = TraceBuilder()
    for _ in range(MAX_TRACE_STEPS):
        trace.narration("가" * MAX_NARRATION_CHARS)
        trace.started("command")
        trace.completed("command")
    result = trace.finish("done")
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 16_384
    assert result["truncated"] > 0
