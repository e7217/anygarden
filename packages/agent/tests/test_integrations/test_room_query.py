"""Tests for room_query module — representative agent opinion collection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doorae_agent.integrations.room_query import (
    RoomQuery,
    _strip_room_mention,
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


class TestStripRoomMention:
    """The forwarded ``[ROOM_QUERY]`` content must not carry the
    ``<#room:...>`` token that triggered the original routing —
    leaving it in causes the server's ``parse_mentions`` to
    re-attach ``room_query`` metadata on the forward, which is the
    infinite loop bug this whole module was patched for."""

    def test_strips_simple_token(self) -> None:
        assert (
            _strip_room_mention("<#room:abc-123> 내일 동탄 날씨는?")
            == "내일 동탄 날씨는?"
        )

    def test_strips_multiple_tokens(self) -> None:
        assert (
            _strip_room_mention("<#room:a> hi <#room:b> there")
            == "hi there"
        )

    def test_leaves_unrelated_text_intact(self) -> None:
        assert _strip_room_mention("그냥 평범한 질문") == "그냥 평범한 질문"

    def test_does_not_eat_user_mention(self) -> None:
        assert (
            _strip_room_mention("<@user:pid> 봐줘 <#room:r1>")
            == "<@user:pid> 봐줘"
        )

    def test_idempotent(self) -> None:
        once = _strip_room_mention("<#room:r1> hi")
        twice = _strip_room_mention(once)
        assert once == twice == "hi"

    def test_handles_token_only_content(self) -> None:
        # Edge case: content was ONLY the routing token. After the
        # strip we have an empty string. ``execute_room_query`` falls
        # back to the original content in that case (so the question
        # isn't lost), but the strip helper itself returns ``""``.
        assert _strip_room_mention("<#room:r1>") == ""


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
    async def test_forward_strips_room_mention_token(self):
        """Regression guard for the infinite forwarding loop. The
        forwarded ``[ROOM_QUERY] ...`` payload must NOT include the
        ``<#room:...>`` token from the original — otherwise the
        server re-attaches ``room_query`` metadata and the target
        room's representative kicks off another hop."""
        client = _make_client()
        query = RoomQuery(
            target_room_id="room-b",
            source_room_id="room-a",
            content="<#room:room-b> 내일 동탄 날씨는?",
        )

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        client.send.assert_called_once_with(
            "room-b",
            "[ROOM_QUERY] 내일 동탄 날씨는?",
        )

    @pytest.mark.asyncio
    async def test_forward_falls_back_when_strip_empties_content(self):
        """If the original content was *only* the routing token,
        stripping leaves an empty string. The fallback restores the
        original so we never send ``"[ROOM_QUERY] "`` (which is
        useless to the target room agents)."""
        client = _make_client()
        query = RoomQuery(
            target_room_id="room-b",
            source_room_id="room-a",
            content="<#room:room-b>",
        )

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        client.send.assert_called_once_with(
            "room-b",
            "[ROOM_QUERY] <#room:room-b>",
        )

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
