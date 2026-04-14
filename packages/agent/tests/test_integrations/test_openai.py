"""Mock integration tests for the OpenAI adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doorae_agent.integrations.openai import OpenAIAdapter, integrate_with_openai


class TestOpenAIAdapter:
    @pytest.mark.asyncio
    async def test_start_without_openai_installed(self) -> None:
        """Adapter starts gracefully when openai is not installed."""
        adapter = OpenAIAdapter(model="gpt-4o")

        # Patch the import to simulate openai not being installed
        with patch.dict("sys.modules", {"openai": None}):
            # Force re-import failure
            adapter._client = None
            adapter._openai = None

        # Without openai, on_message should return None
        result = await adapter.on_message({
            "content": "Hello",
            "room_id": "r1",
        })
        assert result is None

    @pytest.mark.asyncio
    async def test_on_message_with_mock_openai(self) -> None:
        """on_message calls OpenAI API and returns the response."""
        adapter = OpenAIAdapter(model="gpt-4o", system_prompt="Test prompt")

        # Mock the OpenAI client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Mocked reply"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        adapter._client = mock_client
        adapter._openai = MagicMock()

        result = await adapter.on_message({
            "content": "Hello bot",
            "room_id": "room-1",
        })

        assert result == "Mocked reply"
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4o"
        # System prompt + 1 user message
        assert len(call_kwargs["messages"]) == 2
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][1]["content"] == "Hello bot"


class TestIntegrateWithOpenAI:
    @pytest.mark.asyncio
    async def test_integrate_registers_handler(self) -> None:
        """integrate_with_openai registers a message handler on the client."""
        from doorae_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
        assert len(client._message_handlers) == 0

        adapter = await integrate_with_openai(client, model="gpt-4o")

        assert len(client._message_handlers) == 1
        assert isinstance(adapter, OpenAIAdapter)
