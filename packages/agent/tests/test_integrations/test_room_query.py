"""Tests for room_query module — representative agent opinion collection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doorae_agent.integrations.room_query import (
    RoomQuery,
    execute_room_query,
    parse_room_query,
)


def _make_client(my_pids: set | None = None):
    client = MagicMock()
    client._my_participant_ids = my_pids or {"my-pid"}
    client._tasks = {"room-a": MagicMock(), "room-b": MagicMock()}
    client._message_handlers = []
    client.send = AsyncMock()
    client.join_room = AsyncMock()
    client.get_room_participants = AsyncMock(return_value=[
        {"id": "my-pid", "kind": "agent"},
        {"id": "agent-d-pid", "kind": "agent"},
        {"id": "agent-e-pid", "kind": "agent"},
    ])
    return client


class TestParseRoomQuery:
    def test_parse_with_room_query(self):
        msg = {
            "content": "<#room:xyz> 의견?",
            "metadata": {
                "room_query": {"target_room_id": "xyz", "source_room_id": "abc"},
            },
        }
        rq = parse_room_query(msg)
        assert rq is not None
        assert rq.target_room_id == "xyz"
        assert rq.source_room_id == "abc"

    def test_parse_without_room_query(self):
        msg = {"content": "hello", "metadata": {}}
        assert parse_room_query(msg) is None

    def test_parse_no_metadata(self):
        msg = {"content": "hello"}
        assert parse_room_query(msg) is None


class TestExecuteRoomQuery:
    @pytest.mark.asyncio
    async def test_sends_room_query_to_target(self):
        """Representative sends [ROOM_QUERY] to target room."""
        client = _make_client()
        query = RoomQuery(
            target_room_id="room-b",
            source_room_id="room-a",
            content="API 설계 의견?",
        )

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        # Should send [ROOM_QUERY] to target room
        client.send.assert_called_once_with(
            "room-b",
            "[ROOM_QUERY] API 설계 의견?",
        )
        # Should register a message handler
        assert len(client._message_handlers) == 1

    @pytest.mark.asyncio
    async def test_solo_representative_returns_early(self):
        """If no other agents in target room, return early."""
        client = _make_client()
        # Only self in the room
        client.get_room_participants = AsyncMock(return_value=[
            {"id": "my-pid", "kind": "agent"},
        ])
        query = RoomQuery(
            target_room_id="room-b",
            source_room_id="room-a",
            content="질문",
        )

        await execute_room_query(client, {}, query)

        # Should NOT send anything (solo mode)
        client.send.assert_not_called()
        assert len(client._message_handlers) == 0

    @pytest.mark.asyncio
    async def test_callback_collects_and_synthesizes(self):
        """Callback collects all responses then delivers summary."""
        client = _make_client()
        query = RoomQuery(
            target_room_id="room-b",
            source_room_id="room-a",
            content="API 의견?",
        )

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        handler = client._message_handlers[0]
        client.send.reset_mock()

        # Simulate agent D response
        await handler({"room_id": "room-b", "participant_id": "agent-d-pid", "content": "GraphQL이 좋겠습니다"})
        # Not yet complete (1/2)
        assert client.send.call_count == 0

        # Simulate agent E response
        await handler({"room_id": "room-b", "participant_id": "agent-e-pid", "content": "REST가 더 적합합니다"})
        # Now complete (2/2) — should synthesize and deliver
        assert client.send.call_count == 1
        call_args = client.send.call_args
        assert call_args[0][0] == "room-a"  # delivered to source room
        assert "취합 결과" in call_args[0][1]
        assert "2/2" in call_args[0][1]
