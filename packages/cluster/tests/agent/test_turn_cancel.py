"""ENG-09 — a server ``turn_cancel`` frame stops a general turn's engine call."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from anygarden.agent.client import ChatClient
from anygarden.agent.protocol import frames as sdk_frames
from anygarden.agent.runtime.handler_wrapper import RoomHandlerSupervisor

pytestmark = pytest.mark.req("ENG-09")


def _frame(**overrides):
    frame = {"type": "turn_cancel", "agent_id": "agent-1", "room_id": "room",
             "request_id": "req-1", "attempt": 1, "generation": 3, "reason": "user_cancelled"}
    frame.update(overrides)
    return frame


def test_turn_cancel_frame_is_strict_and_separate_from_execution_stop() -> None:
    frame = sdk_frames.TurnCancelOut.model_validate(_frame())
    assert frame.type == "turn_cancel"
    with pytest.raises(ValueError):
        sdk_frames.TurnCancelOut.model_validate(_frame(execution_id="x"))
    # Execution stops keep their required exact identity.
    with pytest.raises(ValueError):
        sdk_frames.TurnStopOut.model_validate({**_frame(), "type": "turn_stop", "stop_id": "s" * 36})


def _client_with_supervisor():
    client = ChatClient("ws://test", token="t")
    client._agent_id = "agent-1"
    client._generation = 3
    supervisor = RoomHandlerSupervisor(client, "echo", 30)
    client._turn_supervisor = supervisor
    client.sendLifecycle = AsyncMock()
    return client, supervisor


@pytest.mark.asyncio
async def test_turn_cancel_cancels_the_running_engine_call() -> None:
    client, supervisor = _client_with_supervisor()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def engine():
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "late reply"

    context = {"turn_attempt": 1, "turn_generation": 3, "turn_lease": "lease"}
    run = asyncio.create_task(supervisor.dispatch("room", "req-1", engine, turn_context=context))
    await asyncio.wait_for(entered.wait(), 1)
    await client._process_frame("room", _frame())
    await asyncio.wait_for(run, 2)
    assert cancelled.is_set()
    outcomes = [c.kwargs.get("outcome") for c in client.sendLifecycle.await_args_list
                if c.kwargs.get("event") == "handler_finished"]
    assert outcomes == ["cancelled"]


@pytest.mark.asyncio
async def test_turn_cancel_before_start_skips_the_turn() -> None:
    client, supervisor = _client_with_supervisor()
    engine = AsyncMock(return_value="reply")
    await client._process_frame("room", _frame())
    await supervisor.dispatch("room", "req-1", engine,
                              turn_context={"turn_attempt": 1, "turn_generation": 3, "turn_lease": "l"})
    engine.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"agent_id": "someone-else"}, {"room_id": "other-room"}, {"generation": 2}, {"attempt": 0},
])
async def test_turn_cancel_for_another_identity_is_ignored(overrides) -> None:
    client, supervisor = _client_with_supervisor()
    await client._process_frame("room", _frame(**overrides))
    assert supervisor._cancelled_turns == set()
