"""OpenHands integration (conceptual -- openhands-ai 0.40.x EventStream API)."""

from __future__ import annotations

from typing import Any

import structlog

from doorae_agent.client import ChatClient
from doorae_agent.integrations.base import EngineAdapter

logger = structlog.get_logger(__name__)


class OpenHandsAdapter(EngineAdapter):
    """Adapter that bridges Doorae messages to the OpenHands EventStream.

    Status: conceptual -- openhands-ai is imported lazily.
    """

    def __init__(
        self,
        runtime_config: dict[str, Any] | None = None,
    ) -> None:
        self._runtime_config = runtime_config or {}
        self._runtime: Any = None

    async def start(self) -> None:
        """Try to import and initialize the OpenHands runtime."""
        try:
            from openhands.runtime import EventStreamRuntime  # type: ignore[import-untyped]

            self._runtime = EventStreamRuntime(**self._runtime_config)
            logger.info("openhands.initialized")
        except ImportError:
            logger.warning(
                "openhands.not_installed",
                hint="pip install openhands-ai",
            )
            self._runtime = None

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Forward the message to the OpenHands EventStream."""
        if self._runtime is None:
            return None

        content = msg.get("content", "")
        if not content:
            return None

        try:
            # Conceptual: actual API depends on openhands-ai version
            event = {"type": "message", "content": content}
            result = await self._runtime.submit(event)
            return str(result)
        except Exception as exc:
            logger.error("openhands.error", error=str(exc))
            return None

    async def stop(self) -> None:
        if self._runtime is not None:
            try:
                await self._runtime.close()
            except Exception:
                pass
        self._runtime = None


async def integrate_with_openhands(
    client: ChatClient,
    runtime_config: dict[str, Any] | None = None,
) -> OpenHandsAdapter:
    """Hook incoming messages to OpenHands EventStream.

    Returns the adapter instance for lifecycle management.
    """
    adapter = OpenHandsAdapter(runtime_config=runtime_config)
    await adapter.start()

    @client.on_message
    async def _handle(msg: dict[str, Any]) -> None:
        response = await adapter.on_message(msg)
        if response:
            room_id = msg.get("room_id", "")
            await client.send(room_id, response)

    return adapter
