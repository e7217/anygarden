"""Codex integration — app-server based adapter using codex-python SDK.

Uses ``codex.Codex`` to maintain a long-lived app-server process.
Each room gets its own thread, so conversation context is natively
preserved without rebuilding prompt history every message.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from doorae_agent.client import ChatClient
from doorae_agent.integrations.base import EngineAdapter

logger = structlog.get_logger(__name__)


class CodexAdapter(EngineAdapter):
    """Adapter that uses the Codex app-server for persistent sessions.

    Instead of spawning a new ``codex exec`` subprocess per message,
    this adapter keeps one app-server process alive for the lifetime
    of the agent and routes messages via room-scoped threads.
    """

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str = "You are a helpful team member in a multi-agent chat. Answer concisely.",
        sandbox: str = "workspace-write",
        reasoning_effort: str | None = None,
    ) -> None:
        self._model = model or "gpt-5.4"
        self._system_prompt = system_prompt
        self._reasoning_effort = reasoning_effort
        self._sandbox = sandbox
        self._codex: Any = None  # Codex instance
        self._threads: dict[str, Any] = {}  # room_id → Thread

    async def start(self) -> None:
        """Start the Codex client (spawns app-server internally)."""
        try:
            from codex import Codex
        except ImportError:
            logger.warning(
                "codex.sdk_not_found",
                hint="Install: pip install codex-python",
            )
            return

        self._codex = Codex()

        logger.info("codex.client_started")

        # Log AGENTS.md presence for debugging
        try:
            agents_md = Path.cwd().parent / "AGENTS.md"
            if agents_md.is_file():
                logger.info("codex.agents_md_found", path=str(agents_md))
        except Exception:
            pass

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Forward the message to a room-scoped thread."""
        if self._codex is None:
            return None

        content = msg.get("content", "")
        if not content:
            return None

        room_id = msg.get("room_id", "_default")

        try:
            # Get or create thread for this room
            thread = self._threads.get(room_id)
            if thread is None:
                thread = self._codex.start_thread()
                self._threads[room_id] = thread
                logger.info(
                    "codex.thread_created",
                    room_id=room_id,
                )

            # run_text returns the response as a string directly
            response = await asyncio.to_thread(thread.run_text, content)
            return response if response else None
        except Exception as exc:
            logger.error("codex.turn_failed", room_id=room_id, error=str(exc))
            # Remove broken thread so next message creates a fresh one
            self._threads.pop(room_id, None)
            return None

    async def stop(self) -> None:
        """Shut down the Codex client."""
        self._threads.clear()
        if self._codex is not None:
            try:
                self._codex.close()
            except Exception:
                pass
            self._codex = None


async def integrate_with_codex(
    client: ChatClient,
    model: str | None = None,
    system_prompt: str = "You are a helpful team member in a multi-agent chat. Answer concisely.",
    reasoning_effort: str | None = None,
) -> CodexAdapter:
    """Hook incoming messages to the Codex app-server.

    The host machine must have `codex` installed and authenticated.
    Returns the adapter instance for lifecycle management.
    """
    adapter = CodexAdapter(model=model, system_prompt=system_prompt, reasoning_effort=reasoning_effort)
    await adapter.start()

    @client.on_message
    async def _handle(msg: dict[str, Any]) -> None:
        room_id = msg.get("room_id", "")

        # Unified response gate — skip if not addressed to us
        from doorae_agent.integrations.base import should_respond
        if not should_respond(msg, client):
            return

        # Check for /delegate command before LLM call
        from doorae_agent.integrations.delegate import parse_delegate, execute_delegate
        delegate = parse_delegate(msg.get("content", ""))
        if delegate:
            await execute_delegate(client, msg, delegate)
            return

        # Check for room_query (representative agent routing)
        from doorae_agent.integrations.room_query import parse_room_query, execute_room_query
        rq = parse_room_query(msg)
        if rq:
            await execute_room_query(client, msg, rq)
            return

        # Keep typing indicator alive while codex processes
        typing_active = True

        async def _typing_loop() -> None:
            while typing_active:
                await client.sendTyping(room_id, True)
                await asyncio.sleep(2)

        typing_task = asyncio.create_task(_typing_loop())
        try:
            response = await adapter.on_message(msg)
            if response:
                await client.send(room_id, response)
        finally:
            typing_active = False
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            await client.sendTyping(room_id, False)

    return adapter
