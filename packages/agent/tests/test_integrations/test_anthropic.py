"""Mock integration tests for the Anthropic adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from doorae_agent.integrations.anthropic import AnthropicAdapter, integrate_with_anthropic


class TestAnthropicAdapter:
    @pytest.mark.asyncio
    async def test_start_without_anthropic_installed(self) -> None:
        """Adapter starts gracefully when anthropic is not installed."""
        adapter = AnthropicAdapter(model="claude-sonnet-4-20250514")
        # Simulate import failure by clearing the client
        adapter._client = None
        adapter._anthropic = None

        # Without anthropic, on_message should return None
        result = await adapter.on_message({
            "content": "Hello",
            "room_id": "r1",
        })
        assert result is None

    @pytest.mark.asyncio
    async def test_on_message_with_mock_anthropic(self) -> None:
        """on_message calls Anthropic API and returns the response."""
        adapter = AnthropicAdapter(model="claude-sonnet-4-20250514", system_prompt="Test prompt")

        # Mock a content block with a text attribute
        mock_block = MagicMock()
        mock_block.text = "Mocked Anthropic reply"

        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        adapter._client = mock_client
        adapter._anthropic = MagicMock()

        result = await adapter.on_message({
            "content": "Hello bot",
            "room_id": "room-1",
        })

        assert result == "Mocked Anthropic reply"
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["system"] == "Test prompt"
        # messages list is mutated after the call (assistant reply appended),
        # so verify the first entry is the user message
        assert call_kwargs["messages"][0]["role"] == "user"
        assert call_kwargs["messages"][0]["content"] == "Hello bot"


class TestIntegrateWithAnthropic:
    @pytest.mark.asyncio
    async def test_integrate_registers_handler(self) -> None:
        """integrate_with_anthropic registers a message handler on the client."""
        from doorae_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
        assert len(client._message_handlers) == 0

        adapter = await integrate_with_anthropic(client, model="claude-sonnet-4-20250514")

        assert len(client._message_handlers) == 1
        assert isinstance(adapter, AnthropicAdapter)
