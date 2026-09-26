"""Map selected runtime metadata to public, ephemeral typing stages."""

from typing import Literal

ProgressStage = Literal["preparing", "using_tool", "writing"]


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
    return None
