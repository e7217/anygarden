"""Mock integration tests for the Deep Agents adapter."""

from __future__ import annotations

import pytest

from doorae_agent.integrations.deep_agents import DeepAgentsAdapter, integrate_with_deep_agents


class TestDeepAgentsAdapter:
    @pytest.mark.asyncio
    async def test_start_without_langgraph_installed(self) -> None:
        """Adapter starts gracefully when langgraph is not installed."""
        adapter = DeepAgentsAdapter()
        await adapter.start()
        # langgraph is not installed, so graph should be None
        assert adapter._graph is None

    @pytest.mark.asyncio
    async def test_on_message_returns_none_without_graph(self) -> None:
        """on_message returns None when the LangGraph graph is not available."""
        adapter = DeepAgentsAdapter()
        await adapter.start()

        result = await adapter.on_message({
            "type": "message",
            "content": "Hello",
            "room_id": "r1",
            "participant_id": "p1",
            "seq": 1,
        })
        assert result is None


class TestIntegrateWithDeepAgents:
    @pytest.mark.asyncio
    async def test_integrate_registers_handler(self) -> None:
        """integrate_with_deep_agents registers a message handler on the client."""
        from doorae_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
        assert len(client._message_handlers) == 0

        adapter = await integrate_with_deep_agents(client)

        assert len(client._message_handlers) == 1
        assert isinstance(adapter, DeepAgentsAdapter)
