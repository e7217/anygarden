"""Mock integration tests for the OpenHands adapter."""

from __future__ import annotations

import pytest

from doorae_agent.integrations.openhands import OpenHandsAdapter, integrate_with_openhands


class TestOpenHandsAdapter:
    @pytest.mark.asyncio
    async def test_start_without_openhands_installed(self) -> None:
        """Adapter starts gracefully when openhands-ai is not installed."""
        adapter = OpenHandsAdapter()
        await adapter.start()
        # openhands is not installed, so runtime should be None
        assert adapter._runtime is None

    @pytest.mark.asyncio
    async def test_on_message_returns_none_without_runtime(self) -> None:
        """on_message returns None when the OpenHands runtime is not available."""
        adapter = OpenHandsAdapter()
        await adapter.start()

        result = await adapter.on_message({
            "type": "message",
            "content": "Hello",
            "room_id": "r1",
            "participant_id": "p1",
            "seq": 1,
        })
        assert result is None


class TestIntegrateWithOpenHands:
    @pytest.mark.asyncio
    async def test_integrate_registers_handler(self) -> None:
        """integrate_with_openhands registers a message handler on the client."""
        from doorae_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
        assert len(client._message_handlers) == 0

        adapter = await integrate_with_openhands(client)

        assert len(client._message_handlers) == 1
        assert isinstance(adapter, OpenHandsAdapter)
