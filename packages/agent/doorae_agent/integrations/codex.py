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
from doorae_agent.coordination.pending_context import (
    append_context_line,
    drain_context,
    format_context_line,
)
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
        # Per-room pending context buffer (#74 Stage B). Stashed by
        # ``ingest_context``; rendered into the next ``on_message``
        # prompt prefix so the Codex thread picks it up as user
        # context for the turn. Since each thread carries its own
        # history natively, one-shot prefix is enough.
        self._pending_context: dict[str, list[tuple[float, str]]] = {}
        # Issue #134 — ThreadStartOptions class is resolved at start()
        # time (not import time) so tests can stub the ``codex`` module
        # with a MagicMock without needing to also stub the nested
        # ``codex.options`` submodule. Stays ``None`` until ``start()``
        # succeeds.
        self._thread_options_cls: Any = None

    async def start(self) -> None:
        """Start the Codex client (spawns app-server internally)."""
        try:
            from codex import Codex
            from codex.options import ThreadStartOptions
        except ImportError:
            logger.warning(
                "codex.sdk_not_found",
                hint="Install: pip install codex-python",
            )
            return

        self._codex = Codex()
        self._thread_options_cls = ThreadStartOptions

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
                # Issue #134 — bypass approval gates for tool calls.
                # Codex otherwise prompts per tool invocation, which
                # a headless agent can never answer. This mirrors
                # the trust model applied to gemini-cli
                # (``--approval-mode yolo``) and claude-code
                # (``permission_mode="bypassPermissions"``).
                # ``sandbox=workspace-write`` stays so the agent
                # can write to its own workspace but can't escape
                # to the host filesystem.
                #
                # When ``_thread_options_cls`` is None (real SDK not
                # installed, or tests that bypass start() setup) the
                # call degrades to the legacy signature so nothing
                # breaks hard.
                if self._thread_options_cls is not None:
                    thread = self._codex.start_thread(
                        options=self._thread_options_cls(
                            approval_policy="never",
                            sandbox=self._sandbox,
                        ),
                    )
                else:
                    thread = self._codex.start_thread()
                self._threads[room_id] = thread
                logger.info(
                    "codex.thread_created",
                    room_id=room_id,
                    approval_policy="never",
                    sandbox=self._sandbox,
                )

            # #74: drain pending context into a prefix so ingested
            # breadcrumbs land in this turn's user content before
            # the actual question.
            prefix = drain_context(self._pending_context, room_id)
            turn_content = f"{prefix}\n\n{content}" if prefix else content

            # run_text returns the response as a string directly
            response = await asyncio.to_thread(thread.run_text, turn_content)
            return response if response else None
        except Exception as exc:
            logger.error("codex.turn_failed", room_id=room_id, error=str(exc))
            # Remove broken thread so next message creates a fresh one
            self._threads.pop(room_id, None)
            return None

    async def ingest_context(self, msg: dict[str, Any]) -> None:
        """Buffer an ``INGEST_ONLY`` message for the next active turn.

        Codex threads already persist history natively, so we only
        need to make sure the breadcrumb lands as part of the next
        ``thread.run_text`` call. Prepended in ``on_message``.
        """
        room_id = msg.get("room_id") or "_default"
        line = format_context_line(msg)
        if line is None:
            return
        append_context_line(self._pending_context, room_id, line)

    async def stop(self) -> None:
        """Shut down the Codex client."""
        self._threads.clear()
        self._pending_context.clear()
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

        # 3-state gate (#74). SKIP drops; INGEST_ONLY stashes for
        # next-turn prefix; RESPOND proceeds. Stage B promotion is
        # decided inside ``decide_policy`` via the accumulator env
        # flag — no extra wiring here.
        from doorae_agent.integrations.base import MessagePolicy, decide_policy
        policy = decide_policy(msg, client)
        if policy is MessagePolicy.SKIP:
            return
        if policy is MessagePolicy.INGEST_ONLY:
            await adapter.ingest_context(msg)
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
