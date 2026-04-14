"""Deep Agents integration (conceptual -- langgraph + deepagents)."""

from __future__ import annotations

from typing import Any

import structlog

from doorae_agent.client import ChatClient
from doorae_agent.integrations.base import EngineAdapter

logger = structlog.get_logger(__name__)


class DeepAgentsAdapter(EngineAdapter):
    """Adapter that bridges Doorae messages to a LangGraph-based agent graph.

    Status: conceptual -- requires langgraph and deepagents packages.
    """

    def __init__(
        self,
        graph_config: dict[str, Any] | None = None,
    ) -> None:
        self._graph_config = graph_config or {}
        self._graph: Any = None

    async def start(self) -> None:
        """Try to import and initialize the LangGraph graph."""
        try:
            from langgraph.graph import StateGraph  # type: ignore[import-untyped]

            # Conceptual: build a minimal graph from config
            self._graph = StateGraph(dict)
            logger.info("deep_agents.initialized")
        except ImportError:
            logger.warning(
                "deep_agents.not_installed",
                hint="pip install langgraph deepagents",
            )
            self._graph = None

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Forward the message through the LangGraph agent graph."""
        if self._graph is None:
            return None

        content = msg.get("content", "")
        if not content:
            return None

        try:
            # Conceptual: invoke the compiled graph with the message
            state = {"messages": [{"role": "user", "content": content}]}
            state.update(self._graph_config)
            result = self._graph.invoke(state)
            # Extract the last assistant message from the result
            messages = result.get("messages", [])
            if messages:
                last = messages[-1]
                return last.get("content", str(last)) if isinstance(last, dict) else str(last)
            return None
        except Exception as exc:
            logger.error("deep_agents.error", error=str(exc))
            return None

    async def stop(self) -> None:
        self._graph = None


async def integrate_with_deep_agents(
    client: ChatClient,
    graph_config: dict[str, Any] | None = None,
) -> DeepAgentsAdapter:
    """Hook incoming messages to a LangGraph-based agent.

    Returns the adapter instance for lifecycle management.
    """
    adapter = DeepAgentsAdapter(graph_config=graph_config)
    await adapter.start()

    @client.on_message
    async def _handle(msg: dict[str, Any]) -> None:
        response = await adapter.on_message(msg)
        if response:
            room_id = msg.get("room_id", "")
            await client.send(room_id, response)

    return adapter
