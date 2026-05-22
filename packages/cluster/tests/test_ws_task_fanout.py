"""Tests for the WS task.updated frame + per-user fanout (#266 Step 6).

The 1차 view (룸 사이드바) is already covered by the existing room
broadcast path. This file validates the *new* user-scoped channel that
backs the 2차 view (에이전트 프로필 Tasks 탭): when a task is created,
updated, or deleted, every active WS subscription owned by an admin
user must receive a ``task.updated`` frame regardless of which room
that subscription lives in.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from anygarden.ws.manager import ConnectionManager
from anygarden.ws.protocol import TaskUpdateOut


class _FakeWS:
    """Minimal WebSocket double — captures every send_text payload."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_task_update_frame_serializes() -> None:
    frame = TaskUpdateOut(
        event="created",
        task={
            "id": "t1",
            "room_id": "r1",
            "agent_id": "a1",
            "title": "x",
            "status": "todo",
            "assignee_participant_id": "p1",
        },
    )
    payload = json.loads(frame.model_dump_json())
    assert payload["type"] == "task.updated"
    assert payload["event"] == "created"
    assert payload["task"]["id"] == "t1"


@pytest.mark.asyncio
async def test_push_to_users_targets_only_listed_users() -> None:
    mgr = ConnectionManager()
    ws_alice = _FakeWS()
    ws_bob = _FakeWS()
    ws_carol = _FakeWS()

    # Two participants for Alice (mimicking two open tabs / rooms),
    # one each for Bob and Carol.
    await mgr.subscribe(
        "room1", "alice-p1", ws_alice, user_id="alice"
    )  # type: ignore[call-arg]
    await mgr.subscribe(
        "room2", "alice-p2", ws_alice, user_id="alice"
    )  # type: ignore[call-arg]
    await mgr.subscribe(
        "room1", "bob-p1", ws_bob, user_id="bob"
    )  # type: ignore[call-arg]
    await mgr.subscribe(
        "room1", "carol-p1", ws_carol, user_id="carol"
    )  # type: ignore[call-arg]

    # Alice has two subscriptions but each gets the frame at most
    # once per call — so 2 sends for the {alice} target set.
    frame = TaskUpdateOut(event="updated", task={"id": "t", "status": "done"})
    await mgr.push_to_users({"alice"}, frame)

    # Alice (both subs) and only Alice receive it.
    assert len(ws_alice.sent) == 2
    assert ws_bob.sent == []
    assert ws_carol.sent == []


@pytest.mark.asyncio
async def test_push_to_users_skips_dead_connections() -> None:
    """A failing send must not crash the loop or drop frames for
    healthy listeners."""
    mgr = ConnectionManager()

    class _BrokenWS:
        async def send_text(self, data: str) -> None:
            raise RuntimeError("connection lost")

    healthy = _FakeWS()
    await mgr.subscribe(
        "r", "broken-p", _BrokenWS(), user_id="u"
    )  # type: ignore[call-arg]
    await mgr.subscribe(
        "r", "healthy-p", healthy, user_id="u"
    )  # type: ignore[call-arg]

    frame = TaskUpdateOut(event="created", task={"id": "x"})
    # Must not raise.
    await mgr.push_to_users({"u"}, frame)

    # Healthy ws still got the frame.
    assert len(healthy.sent) == 1


@pytest.mark.asyncio
async def test_unsubscribe_clears_user_index() -> None:
    mgr = ConnectionManager()
    ws = _FakeWS()
    await mgr.subscribe(
        "r", "p", ws, user_id="u"
    )  # type: ignore[call-arg]
    await mgr.unsubscribe("p")

    frame = TaskUpdateOut(event="deleted", task={"id": "x"})
    await mgr.push_to_users({"u"}, frame)
    assert ws.sent == []


@pytest.mark.asyncio
async def test_subscribe_without_user_id_still_works_for_room_broadcast() -> None:
    """Backwards compatibility: existing call sites that don't pass
    ``user_id`` (e.g. agent participants, anonymous guests) continue
    to subscribe normally — they just don't appear in the user
    index, so a per-user fanout doesn't reach them."""
    mgr = ConnectionManager()
    ws = _FakeWS()
    await mgr.subscribe("r", "p", ws)
    # Room broadcast still reaches them.
    from anygarden.ws.protocol import TypingOut

    await mgr.broadcast(
        "r", TypingOut(room_id="r", participant_id="p", is_typing=True)
    )
    assert len(ws.sent) == 1
    # User fanout does not.
    await mgr.push_to_users({"u"}, TaskUpdateOut(event="created", task={"id": "x"}))
    assert len(ws.sent) == 1
