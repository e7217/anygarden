"""OpenAI engine integration — direct API calls via the openai library."""

from __future__ import annotations

from typing import Any

import structlog

from doorae_agent.client import ChatClient
from doorae_agent.integrations.base import EngineAdapter

logger = structlog.get_logger(__name__)


class OpenAIAdapter(EngineAdapter):
    """Adapter that bridges Doorae messages to OpenAI's Chat Completions API."""

    def __init__(
        self,
        model: str = "gpt-4o",
        system_prompt: str = "You are a helpful assistant.",
        max_history: int = 20,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._max_history = max_history
        self._openai = None
        self._client = None
        # room_id -> conversation history
        self._conversations: dict[str, list[dict[str, str]]] = {}

    async def start(self) -> None:
        """Import and initialize the OpenAI client."""
        try:
            import openai  # type: ignore[import-untyped]
            self._openai = openai
            self._client = openai.AsyncOpenAI()
            logger.info("openai.initialized", model=self._model)
        except ImportError:
            logger.warning(
                "openai.not_installed",
                hint="pip install doorae-agent[openai]",
            )
            self._client = None

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Append the message to conversation history and call OpenAI."""
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
            history[:] = history[-self._max_history:]

        # Build messages with system prompt
        messages = [{"role": "system", "content": self._system_prompt}] + history

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
            reply = response.choices[0].message.content or ""
            # Append assistant response to history
            history.append({"role": "assistant", "content": reply})
            return reply
        except Exception as exc:
            logger.error("openai.error", error=str(exc))
            # Remove the user message on failure to keep history consistent
            if history and history[-1]["role"] == "user":
                history.pop()
            return None

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
        self._conversations.clear()


async def integrate_with_openai(
    client: ChatClient,
    model: str = "gpt-4o",
    system_prompt: str = "You are a helpful assistant.",
) -> OpenAIAdapter:
    """Hook incoming messages to OpenAI Chat Completions.

    Returns the adapter instance for lifecycle management.
    """
    adapter = OpenAIAdapter(
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
