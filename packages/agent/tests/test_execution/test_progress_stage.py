from unittest.mock import AsyncMock

import pytest

from anygarden_agent.integrations.room_execution import RoomExecutionAdapter
from anygarden_agent.runtime.execution.progress_stage import stage_for_progress


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
