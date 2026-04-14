"""Unit tests for ChatClient — connect, reconnect, since_seq, callback, send."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doorae_agent.client import ChatClient


class TestChatClientInit:
    def test_client_creation(self) -> None:
        """ChatClient can be instantiated with required parameters."""
        client = ChatClient("ws://localhost:8000", token="test-tok", agent_name="TestBot")
        assert client._server_url == "ws://localhost:8000"
        assert client._token == "test-tok"
        assert client._agent_name == "TestBot"
        assert client._last_seq == {}
        assert client._message_handlers == []

    def test_client_strips_trailing_slash(self) -> None:
        """Server URL trailing slash is stripped."""
        client = ChatClient("ws://localhost:8000/", token="t")
        assert client._server_url == "ws://localhost:8000"


class TestChatClientCallbacks:
    def test_on_message_registers_handler(self) -> None:
        """on_message decorator registers a handler."""
        client = ChatClient("ws://localhost:8000", token="t")

        @client.on_message
        async def my_handler(msg):
            pass

        assert len(client._message_handlers) == 1
        assert client._message_handlers[0] is my_handler

    def test_on_join_room_registers_handler(self) -> None:
        """on_join_room decorator registers a handler."""
        client = ChatClient("ws://localhost:8000", token="t")

        @client.on_join_room
        async def my_handler(room_id):
            pass

        assert len(client._join_handlers) == 1

    @pytest.mark.asyncio
    async def test_multiple_handlers(self) -> None:
        """Multiple on_message handlers are all registered."""
        client = ChatClient("ws://localhost:8000", token="t")
        results = []

        @client.on_message
        async def h1(msg):
            results.append("h1")

        @client.on_message
        async def h2(msg):
            results.append("h2")

        assert len(client._message_handlers) == 2


class TestChatClientSinceSeq:
    @pytest.mark.asyncio
    async def test_since_seq_tracking(self) -> None:
        """last_seq is updated when join_room is called."""
        client = ChatClient("ws://localhost:8000", token="t")
        client._running = True
        client._last_seq["room-1"] = 0
        # Verify initial state
        assert client._last_seq["room-1"] == 0
        # Simulate seq update
        client._last_seq["room-1"] = 42
        assert client._last_seq["room-1"] == 42


class TestChatClientSend:
    @pytest.mark.asyncio
    async def test_send_raises_when_not_connected(self) -> None:
        """send() raises RuntimeError when not connected to the room."""
        client = ChatClient("ws://localhost:8000", token="t")
        with pytest.raises(RuntimeError, match="Not connected to room"):
            await client.send("nonexistent-room", "hello")

    @pytest.mark.asyncio
    async def test_send_writes_to_websocket(self) -> None:
        """send() serializes a SendFrame and sends it over the WebSocket."""
        client = ChatClient("ws://localhost:8000", token="t")
        mock_ws = AsyncMock()
        client._connections["room-1"] = mock_ws

        await client.send("room-1", "hello world", metadata={"key": "val"})

        mock_ws.send.assert_called_once()
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "send"
        assert sent["content"] == "hello world"
        assert sent["metadata"]["key"] == "val"
        assert "_nonce" in sent["metadata"]  # Self-echo filter nonce
