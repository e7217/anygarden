"""Anthropic Messages API integration via the anthropic library."""

from __future__ import annotations

from typing import Any

import structlog

from doorae_agent.client import ChatClient
from doorae_agent.integrations.base import EngineAdapter

logger = structlog.get_logger(__name__)


class AnthropicAdapter(EngineAdapter):
    """Adapter that bridges Doorae messages to Anthropic's Messages API."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        system_prompt: str = "You are a helpful assistant.",
        max_history: int = 20,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._max_history = max_history
        self._anthropic: Any = None
        self._client: Any = None
        # room_id -> conversation history
        self._conversations: dict[str, list[dict[str, str]]] = {}

    async def start(self) -> None:
        """Import and initialize the Anthropic client."""
        try:
            import anthropic  # type: ignore[import-untyped]

            self._anthropic = anthropic
            self._client = anthropic.AsyncAnthropic()
            logger.info("anthropic.initialized", model=self._model)
        except ImportError:
            logger.warning(
                "anthropic.not_installed",
                hint="pip install doorae-agent[anthropic]",
            )
            self._client = None

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Append the message to conversation history and call Anthropic."""
        if self._client is None:
            return None

        content = msg.get("content", "")
        if not content:
            return None

        room_id = msg.get("room_id", "default")
        history = self._conversations.setdefault(room_id, [])

        # Append user message
        history.append({"role": "user", "content": content})

        # Trim history to max length
        if len(history) > self._max_history:
            history[:] = history[-self._max_history :]

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=self._system_prompt,
                messages=history,
            )
            # Extract text from the response content blocks
            reply = ""
            for block in response.content:
                if hasattr(block, "text"):
                    reply += block.text
            # Append assistant response to history
            history.append({"role": "assistant", "content": reply})
            return reply
        except Exception as exc:
            logger.error("anthropic.error", error=str(exc))
            # Remove the user message to keep alternating user/assistant order
            # Anthropic API requires strict role alternation
            if history and history[-1]["role"] == "user":
                history.pop()
            return None

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
        self._conversations.clear()


async def integrate_with_anthropic(
    client: ChatClient,
    model: str = "claude-sonnet-4-20250514",
    system_prompt: str = "You are a helpful assistant.",
) -> AnthropicAdapter:
    """Hook incoming messages to Anthropic Messages API.

    Returns the adapter instance for lifecycle management.
    """
    adapter = AnthropicAdapter(
        model=model,
        system_prompt=system_prompt,
    )
    await adapter.start()

    @client.on_message
    async def _handle(msg: dict[str, Any]) -> None:
        response = await adapter.on_message(msg)
        if response:
            room_id = msg.get("room_id", "")
            await client.send(room_id, response)

    return adapter
