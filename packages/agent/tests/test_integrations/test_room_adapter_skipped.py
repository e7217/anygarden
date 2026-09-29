"""#720 — a delivered turn the agent declines must be closed as ``skipped``.

The server hands every agent in a room a durable turn for a human send.
When ``decide_policy`` declines it (SKIP / INGEST_ONLY) the handler used to
return silently, leaving the turn leased until its lease expired and
blocking every later delivery to this agent in the room.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from anygarden_agent.integrations import codex_cli
from anygarden_agent.integrations.codex_cli import register_room_adapter

MY_PID = "my-pid"


class _FakeClient:
    def __init__(self) -> None:
        self._agent_name = "agent01"
        self._my_participant_ids = {MY_PID}
        self._agent_id = "agent-1"
        self._context_window_opt_out = False
        self._recent_msgs: dict = {}
        self._speaker_strategy: dict = {}
        self._orchestrator_agent_id: dict = {}
        self.handler: Any = None
        self.sendLifecycle = AsyncMock()
        self.sendTyping = AsyncMock()

    def on_message(self, fn):
        self.handler = fn
        return fn


class _FakeSupervisor:
    dispatch = AsyncMock()

    def __init__(self, **_kwargs) -> None:
        pass


@pytest.fixture()
def wired(monkeypatch):
    _FakeSupervisor.dispatch = AsyncMock()
    monkeypatch.setattr(codex_cli, "RoomHandlerSupervisor", _FakeSupervisor)
    client = _FakeClient()
    adapter = AsyncMock()
    register_room_adapter(client, adapter, "codex-cli", 60)
    return client, adapter


def _msg(content: str, **metadata: Any) -> dict[str, Any]:
    return {
        "room_id": "room-1",
        "participant_id": "human-pid",
        "content": content,
        "metadata": metadata,
    }


_TURN = {"request_id": "rid-1", "turn_attempt": 1, "turn_generation": 2, "turn_lease": "lease"}


@pytest.mark.asyncio
async def test_skip_closes_the_delivered_turn(wired) -> None:
    client, _ = wired
    await client.handler(_msg("<@user:other-pid> 도와줘", mentions=[
        {"type": "user", "id": "other-pid"},
    ], **_TURN))

    client.sendLifecycle.assert_awaited_once_with(
        "room-1", "rid-1", event="handler_finished", outcome="skipped",
    )
    _FakeSupervisor.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_only_absorbs_then_closes_the_turn(wired) -> None:
    client, adapter = wired
    await client.handler(_msg("배경 정보", ingest_only=True, **_TURN))

    adapter.ingest_context.assert_awaited_once()
    client.sendLifecycle.assert_awaited_once_with(
        "room-1", "rid-1", event="handler_finished", outcome="skipped",
    )


@pytest.mark.asyncio
async def test_declined_message_without_a_turn_sends_nothing(wired) -> None:
    client, _ = wired
    await client.handler(_msg("<@user:other-pid> 도와줘", mentions=[
        {"type": "user", "id": "other-pid"},
    ]))

    client.sendLifecycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_respond_does_not_report_skipped(wired) -> None:
    client, _ = wired
    await client.handler(_msg(f"<@user:{MY_PID}> 무엇을 할 수 있나", mentions=[
        {"type": "user", "id": MY_PID},
    ], **_TURN))

    _FakeSupervisor.dispatch.assert_awaited_once()
    client.sendLifecycle.assert_not_awaited()
