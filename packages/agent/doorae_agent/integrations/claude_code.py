"""Claude Code SDK integration.

Uses the Anthropic ``claude-agent-sdk`` Python library (previously
``claude-code-sdk``) to forward Doorae chat messages to Claude Code
and stream back the response.

Per-agent configuration is carried in by Phase 0's materialized
directory layout. The doorae-agent subprocess is spawned with cwd
set to ``~/.doorae/agents/<id>/workspace/`` so this adapter can
lean on ``Path.cwd()`` as the working directory. The materializer
drops:

- ``workspace/AGENTS.md`` → ``../AGENTS.md`` symlink
- ``workspace/CLAUDE.md`` → ``../CLAUDE.md`` symlink (which itself
  points at ``AGENTS.md``)
- ``.claude/settings.json`` with MCP server config and plugin
  enablement
- ``.claude/skills/<name>`` → ``../skills`` symlinks

``query()`` is called with ``ClaudeAgentOptions(cwd=...,
setting_sources=["project"])``. The ``setting_sources`` flag is
**required** — without it the SDK does not load any project-local
configuration at all, including CLAUDE.md. Silent surprise waiting
to happen; pin it explicitly so future refactors don't strip it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from doorae_agent.client import ChatClient
from doorae_agent.integrations.base import EngineAdapter

logger = structlog.get_logger(__name__)


class ClaudeCodeAdapter(EngineAdapter):
    """Adapter that bridges Doorae messages to the Claude Agent SDK."""

    def __init__(
        self,
        agent_name: str = "ClaudeCode",
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> None:
        self._agent_name = agent_name
        # When ``system_prompt`` is None we leave it unset on the
        # options so CLAUDE.md is the sole source of system-level
        # instructions. If the caller passes a string we layer it
        # on top as an additional system prompt.
        self._system_prompt = system_prompt
        self._model = model
        self._sdk: Any = None
        self._options_cls: Any = None
        self._query_fn: Any = None
        # Per-room session id (Claude Agent SDK manages its own
        # conversation state via ``session_id`` / ``resume``). We
        # let the SDK create a fresh session per first message and
        # then reuse the same session id for follow-ups so context
        # persists across turns within a room.
        self._sessions: dict[str, str] = {}

    async def start(self) -> None:
        """Import the claude-agent-sdk and cache the query hook."""
        try:
            import claude_agent_sdk  # type: ignore[import-not-found]
        except ImportError:
            logger.warning(
                "claude_code.not_installed",
                hint="pip install doorae-agent[claude-code]",
            )
            self._sdk = None
            return

        self._sdk = claude_agent_sdk
        self._options_cls = claude_agent_sdk.ClaudeAgentOptions
        self._query_fn = claude_agent_sdk.query
        logger.info(
            "claude_code.initialized",
            version=getattr(claude_agent_sdk, "__version__", "?"),
        )

        # Informational breadcrumb: does the materializer's
        # ``workspace/CLAUDE.md`` symlink exist one level above us?
        # This is the signal that per-agent instructions are wired.
        try:
            link = Path.cwd() / "CLAUDE.md"
            if link.is_symlink() or link.is_file():
                logger.info("claude_code.claude_md_found", path=str(link))
            else:
                logger.debug("claude_code.no_claude_md", cwd=str(Path.cwd()))
        except Exception:
            pass

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Forward the message to claude-agent-sdk and return the reply."""
        if self._sdk is None or self._query_fn is None:
            return None

        content = msg.get("content", "")
        if not content:
            return None

        room_id = msg.get("room_id", "_default")

        try:
            options = self._build_options(room_id)
            reply = await self._collect_reply(content, options)
            return reply
        except Exception as exc:
            logger.error("claude_code.query_failed", error=str(exc))
            return None

    async def stop(self) -> None:
        self._sessions.clear()
        self._sdk = None

    def _build_options(self, room_id: str) -> Any:
        """Construct ClaudeAgentOptions for a given room.

        Key flags:

        - ``cwd`` pinned at ``Path.cwd()`` so the Claude Agent SDK
          discovers the per-agent directory via its parent.
        - ``setting_sources=["project"]`` so CLAUDE.md, project
          skills, and ``.claude/settings.json`` actually load. The
          default of ``None`` silently skips them — that's the
          single most common "why aren't my skills firing?"
          mistake in claude-agent-sdk.
        - ``resume`` carries the per-room session id forward so
          follow-up messages stay in the same conversation.
        """
        kwargs: dict[str, Any] = {
            "cwd": str(Path.cwd()),
            "setting_sources": ["project"],
        }
        if self._system_prompt is not None:
            kwargs["system_prompt"] = self._system_prompt
        if self._model is not None:
            kwargs["model"] = self._model
        session_id = self._sessions.get(room_id)
        if session_id is not None:
            kwargs["resume"] = session_id
        return self._options_cls(**kwargs)

    async def _collect_reply(self, prompt: str, options: Any) -> str | None:
        """Drain ``query()`` and return the final user-facing reply.

        The SDK streams a mix of message types — AssistantMessage
        (with a content list of TextBlock / ToolUseBlock /
        ToolResultBlock / ThinkingBlock), SystemMessage, UserMessage
        (for tool results), ResultMessage, and a few hook/event
        variants. Only two sources carry the answer the agent
        should actually send back to the room:

        1. ``ResultMessage.result`` — the SDK's canonical final
           string for the whole turn. When present it's
           authoritative and we prefer it.
        2. ``AssistantMessage.content`` filtered to ``TextBlock``
           entries only. Tool use/result and thinking blocks are
           intermediate steps and must not leak into the room:
           surfacing them was the bug where a skill file's body
           got echoed as the agent's reply.

        Also captures ``session_id`` so the next per-room turn can
        resume this conversation.
        """
        text_parts: list[str] = []
        result_field: str | None = None
        session_id: str | None = None

        async for message in self._query_fn(prompt=prompt, options=options):
            msg_type = type(message).__name__

            sid = getattr(message, "session_id", None)
            if sid is not None:
                session_id = sid

            # Only harvest text from AssistantMessage content blocks,
            # and only from TextBlock (skip tool use/result/thinking).
            if msg_type == "AssistantMessage":
                content = getattr(message, "content", None) or []
                for block in content:
                    if type(block).__name__ != "TextBlock":
                        continue
                    text = getattr(block, "text", None)
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text)

            elif msg_type == "ResultMessage":
                result = getattr(message, "result", None)
                if isinstance(result, str) and result.strip():
                    result_field = result

        if session_id is not None:
            # Caller (integrate_with_claude_code) promotes this
            # into the per-room session map. Store on the instance
            # so the handler wrapper can grab it without having to
            # return session state alongside the reply.
            self._last_session_id = session_id

        # ResultMessage.result is the SDK's definitive final reply
        # for the turn — prefer it when present. Fall back to the
        # concatenated assistant TextBlocks only if the SDK didn't
        # emit a ResultMessage (some streaming subtypes).
        if result_field:
            return result_field.strip()
        if text_parts:
            return "\n\n".join(part.strip() for part in text_parts).strip()
        return None


async def integrate_with_claude_code(
    client: ChatClient,
    agent_config: dict[str, Any] | None = None,
) -> ClaudeCodeAdapter:
    """Hook incoming messages to the Claude Agent SDK.

    Returns the adapter instance for lifecycle management.
    """
    import asyncio

    config = agent_config or {}
    adapter = ClaudeCodeAdapter(
        agent_name=config.get("name", "ClaudeCode"),
        system_prompt=config.get("system_prompt"),
        model=config.get("model"),
    )
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

        # Keep the typing indicator alive while Claude Code thinks.
        typing_active = True

        async def _typing_loop() -> None:
            while typing_active:
                await client.sendTyping(room_id, True)
                await asyncio.sleep(2)

        typing_task = asyncio.create_task(_typing_loop())
        try:
            response = await adapter.on_message(msg)
            # Promote the last session id captured during query back
            # into the per-room session map, so the next turn can
            # resume the conversation.
            sid = getattr(adapter, "_last_session_id", None)
            if sid is not None and room_id:
                adapter._sessions[room_id] = sid
                adapter._last_session_id = None
            if response:
                await client.send(room_id, response)
        finally:
            typing_active = False
            typing_task.cancel()
            await client.sendTyping(room_id, False)

    return adapter
