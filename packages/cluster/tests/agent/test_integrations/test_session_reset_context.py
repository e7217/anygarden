"""#986 — lines buffered for a session a person cleared never reach the new one."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from anygarden.agent.integrations.room_execution import RoomExecutionAdapter

ROOM = str(uuid.uuid4())


def _snapshot(epoch: int) -> dict:
    return {
        "room_id": ROOM, "memory_md": "", "revision": epoch, "session_epoch": epoch,
        "generation": 1, "scope_version": "room-memory-v1",
    }


def _adapter() -> tuple[RoomExecutionAdapter, SimpleNamespace]:
    adapter = RoomExecutionAdapter()
    client = SimpleNamespace(_room_memory={ROOM: _snapshot(0)}, _generation=1, _rosters={})
    adapter._client = client
    adapter._room_roster = lambda room_id: None
    return adapter, client


def _human(content: str) -> dict:
    return {"room_id": ROOM, "participant_id": "human", "content": content}


@pytest.mark.req("COL-13")
async def test_a_cleared_session_drops_the_lines_buffered_before_it():
    adapter, client = _adapter()
    await adapter.ingest_context(_human("before the clear"))
    client._room_memory[ROOM] = _snapshot(1)
    await adapter.ingest_context(_human("after the clear"))

    lines = [line for _, line in adapter._pending_context[ROOM]]
    assert len(lines) == 1 and "after the clear" in lines[0]


@pytest.mark.req("COL-13")
async def test_a_turn_after_the_clear_does_not_see_older_lines():
    adapter, client = _adapter()
    await adapter.ingest_context(_human("before the clear"))
    client._room_memory[ROOM] = _snapshot(1)

    adapter._forget_context_of_a_cleared_session(ROOM)
    assert ROOM not in adapter._pending_context


@pytest.mark.req("COL-13")
async def test_the_same_session_keeps_its_lines():
    adapter, _ = _adapter()
    await adapter.ingest_context(_human("one"))
    await adapter.ingest_context(_human("two"))
    adapter._forget_context_of_a_cleared_session(ROOM)
    assert len(adapter._pending_context[ROOM]) == 2
