"""OpenHands V1 SDK integration (#355 Phase 0).

Bridges Doorae chat messages to the in-process OpenHands ``Conversation``
runtime instead of spawning a CLI subprocess. Resolves the structural
problems that the three CLI adapters share (#352/#354 MCP exposure
revert, task-transition heuristics, idle/abort detection) by replacing
stdout-parsing with the SDK's typed event stream.

Phase 0 scope (see ``.tmp/plan-355-openhands-engine-migration.md``):

- Adapter wired through ``RoomHandlerSupervisor`` from day one — the
  #292 ``openhands`` removal cited supervisor + context plumbing
  absence as the silent-degradation root cause. We mirror what
  ``claude_code.py`` / ``codex.py`` / ``gemini_cli.py`` already do.
- Per-room ``Conversation`` reuse so multi-turn context persists.
- Multi-provider (Anthropic / OpenAI / Google) via litellm-style
  model prefixes in the catalog (``anthropic/...``, ``openai/...``,
  ``gemini/...``). Credentials bridged via ``secrets_in_env`` so a
  rogue tool call cannot read them off ``/proc/self/environ``.
- MCP server registration, skills export, and DelegateTool sub-agent
  integration are explicitly out of scope here — separate Phase 1/2/3
  PRs follow the same plan.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import structlog

from doorae_agent import secrets as agent_secrets
from doorae_agent.client import ChatClient
from doorae_agent.coordination.pending_context import (
    append_context_line,
    drain_context,
    format_context_line,
)
from doorae_agent.integrations.base import (
    EngineAdapter,
    MessagePolicy,
    compose_session_context_suffix,
    decide_policy,
)
from doorae_agent.runtime.handler_wrapper import RoomHandlerSupervisor


logger = structlog.get_logger(__name__)


# litellm-style provider env keys. ``LLM(api_key=...)`` is also
# acceptable but env-based discovery is consistent with how the
# existing claude_code adapter handles ``ANTHROPIC_*`` and lets the
# same ``secrets_in_env`` context manager cover all three providers
# uniformly. Keys absent from agent_secrets are ignored — the SDK
# then falls back to its own discovery (host env, gcloud ADC, etc.).
_OPENHANDS_SDK_ENV_KEYS = (
    # Anthropic
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    # OpenAI
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    # Google / Gemini
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    # Generic litellm proxy escape hatch (#197 LLM gateway integration
    # is Phase 4; this key is here so a manual export still works).
    "LITELLM_API_KEY",
    "LITELLM_BASE_URL",
)


__all__ = [
    "OpenHandsAdapter",
    "integrate_with_openhands",
]


class OpenHandsAdapter(EngineAdapter):
    """Adapter that bridges Doorae messages to the OpenHands V1 SDK.

    Parallels the structure of ``ClaudeCodeAdapter`` / ``CodexAdapter``:

    - ``_pending_context`` per-room buffer for ``INGEST_ONLY`` messages
      (#74 / #286 contract).
    - Per-room ``Conversation`` instance dict so multi-turn state stays
      attached to the right room.
    - ``start`` lazy-imports the SDK so ``import doorae_agent`` succeeds
      even when ``openhands-sdk`` isn't installed (matching the
      claude-agent-sdk pattern).
    """

    def __init__(
        self,
        agent_name: str = "OpenHands",
        system_prompt: str | None = None,
        model: str | None = None,
        client: ChatClient | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._system_prompt = system_prompt
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._client = client
        # Lazy-imported SDK handles. Set by ``start``.
        self._sdk: Any = None
        self._llm_cls: Any = None
        self._agent_cls: Any = None
        self._conversation_cls: Any = None
        # Per-room ``Conversation`` instances. OpenHands keeps its own
        # event-sourced history, so handing the same instance back per
        # room is enough for multi-turn context.
        self._conversations: dict[str, Any] = {}
        # Per-room pending-context buffer (#74). See base class docstring.
        self._pending_context: dict[str, list[tuple[float, str]]] = {}

    async def start(self) -> None:
        """Import openhands-sdk and cache the constructors.

        Mirrors ``ClaudeCodeAdapter.start``: a missing SDK is logged
        and the adapter degrades to a no-op (``on_message`` returns
        ``None``). Tests inject a fake module via ``sys.modules`` so
        they don't depend on the real package.
        """
        try:
            from openhands.sdk import (  # type: ignore[import-not-found]
                LLM,
                Agent,
                Conversation,
            )
        except ImportError:
            logger.warning(
                "openhands.not_installed",
                hint="pip install openhands-sdk",
            )
            self._sdk = None
            return

        self._sdk = True  # truthiness sentinel; individual classes below
        self._llm_cls = LLM
        self._agent_cls = Agent
        self._conversation_cls = Conversation
        logger.info("openhands.initialized", model=self._model)

    async def stop(self) -> None:
        """Close every per-room conversation and drop SDK handles."""
        for room_id, entry in list(self._conversations.items()):
            # ``_conversations`` stores ``(conversation, captured_list)``
            # tuples — see ``_get_or_create_conversation``.
            conv = entry[0] if isinstance(entry, tuple) else entry
            try:
                close = getattr(conv, "close", None)
                if close is not None:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.warning(
                    "openhands.close_failed",
                    room_id=room_id,
                    error=str(exc),
                )
        self._conversations.clear()
        self._pending_context.clear()
        self._sdk = None

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Forward the message to OpenHands and return the assistant text.

        Pipeline:

        1. ``decide_policy`` was already applied upstream by the handler;
           we still belt-and-suspenders gate on empty content.
        2. Drain ``_pending_context`` and wrap as ``<room_conversation>``
           via ``assemble_user_content`` (#286 contract).
        3. Prepend memory + roster suffix via
           ``compose_session_context_suffix`` (#293 contract). Order
           matches the three CLI adapters so a future fourth context
           layer lands in one place.
        4. Get-or-create the per-room ``Conversation``. The SDK's
           constructor takes ``callbacks=`` so we register a closure
           that captures assistant ``MessageEvent`` payloads.
        5. ``send_message`` queues the prompt; ``run`` drives the
           agent loop. Both are sync calls — wrap with
           ``asyncio.to_thread`` so they don't block the event loop.
        6. Return the captured assistant text (joined when multi-part).
        """
        if self._sdk is None:
            return None

        content = msg.get("content", "")
        if not content:
            return None

        room_id = msg.get("room_id", "_default")

        # Steps 2-3: doorae context plumbing. Identical pipeline to
        # claude_code/codex/gemini_cli — the helpers live on the base
        # class precisely so a new engine adapter inherits the
        # behaviour for free (#286, #293).
        prompt = self.assemble_user_content(room_id, content)
        suffix = compose_session_context_suffix(
            self._client,
            room_id,
            include_roster=True,
            with_collaborative_hint=True,
        )
        if suffix:
            # Order: context first, then user content — same as the
            # CLI adapters' prepend pattern (#293).
            prompt = f"{suffix}\n\n{prompt}"

        try:
            conversation, captured = self._get_or_create_conversation(room_id)
        except Exception as exc:
            logger.error(
                "openhands.conversation_init_failed",
                room_id=room_id,
                error=str(exc),
            )
            return None

        # Steps 4-5: drive the agent. ``secrets_in_env`` covers the
        # SDK construction window; the actual LLM call happens inside
        # ``run`` so we keep the env populated for the duration.
        with agent_secrets.secrets_in_env(list(_OPENHANDS_SDK_ENV_KEYS)):
            try:
                await asyncio.to_thread(conversation.send_message, prompt)
                await asyncio.to_thread(conversation.run)
            except Exception as exc:
                logger.error(
                    "openhands.run_failed",
                    room_id=room_id,
                    error=str(exc),
                )
                return None

        # Step 6: drain captured assistant text. The capture closure
        # appends to a per-conversation list reset between turns.
        text_parts = list(captured)
        captured.clear()
        if not text_parts:
            return None
        return "\n\n".join(p.strip() for p in text_parts if p.strip()).strip() or None

    async def ingest_context(self, msg: dict[str, Any]) -> None:
        """Stash a non-addressed message as context for the next turn.

        Identical pattern to the three CLI adapters: format the line,
        append to the per-room buffer, let ``assemble_user_content``
        drain on the next active turn (#74, #286).
        """
        room_id = msg.get("room_id") or "_default"
        line = format_context_line(msg)
        if line is None:
            return
        append_context_line(self._pending_context, room_id, line)

    def _format_context_line(self, msg: dict[str, Any]) -> str | None:
        """Back-compat wrapper around the shared helper.

        Matches ``ClaudeCodeAdapter._format_context_line`` so any test
        that introspects an adapter via this method name keeps
        working when retargeted at this engine.
        """
        return format_context_line(msg)

    def _drain_pending_context(self, room_id: str) -> str:
        """Back-compat wrapper around the shared helper."""
        return drain_context(self._pending_context, room_id)

    def _build_llm(self) -> Any:
        """Construct the SDK ``LLM`` from adapter config.

        Model strings follow litellm convention with a provider prefix
        (``anthropic/claude-opus-4-7``, ``openai/gpt-5.4``,
        ``gemini/gemini-3-pro-preview``). The catalog is the source of
        truth; this adapter simply forwards what it receives.
        """
        if self._llm_cls is None:
            raise RuntimeError("OpenHands SDK not initialized; call start() first.")
        if not self._model:
            raise RuntimeError(
                "OpenHandsAdapter requires an explicit model string with "
                "provider prefix (e.g. 'anthropic/claude-opus-4-7')."
            )
        # ``LLM(api_key=...)`` accepts a SecretStr; we let the SDK
        # discover the key from os.environ instead so the same
        # secrets_in_env pattern covers all three providers without
        # hard-coding which env var maps to which.
        kwargs: dict[str, Any] = {"model": self._model}
        # Reasoning effort is a Phase 4 concern — different providers
        # name the knob differently. Forward when the SDK accepts it;
        # silently drop otherwise so Phase 0 doesn't crash on
        # provider-specific validation.
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        try:
            return self._llm_cls(**kwargs)
        except TypeError:
            # SDK rejected ``reasoning_effort`` — retry without it.
            kwargs.pop("reasoning_effort", None)
            return self._llm_cls(**kwargs)

    def _get_or_create_conversation(self, room_id: str) -> tuple[Any, list[str]]:
        """Return ``(conversation, captured_text_buffer)`` for *room_id*.

        Lazily constructs a new ``Conversation`` on first use per room.
        The captured buffer is held alongside the conversation so the
        capture callback can append to it without a class-level dict.
        """
        existing = self._conversations.get(room_id)
        if existing is not None:
            return existing  # type: ignore[return-value]

        captured: list[str] = []

        def _capture_assistant(event: Any) -> None:
            """Pull assistant text out of the SDK's typed event stream.

            Defensive type detection — the SDK's event class hierarchy
            lives in ``openhands.sdk.event`` and we don't want a hard
            import dependency just for ``isinstance``. Match by class
            name + attributes instead, the same pattern claude_code
            uses for ``AssistantMessage`` / ``TextBlock``.
            """
            try:
                event_type = type(event).__name__
                # MessageEvent carries (role, content) per the SDK
                # event reference. Only the assistant role contributes
                # to the user-visible reply.
                if event_type != "MessageEvent":
                    return
                role = getattr(event, "role", None) or getattr(
                    getattr(event, "message", None), "role", None
                )
                if role != "assistant":
                    return
                # Content can be a plain string or a list of parts; we
                # accept both shapes so the capture survives minor SDK
                # reshapes without a code change.
                text: str | None = None
                content = getattr(event, "content", None)
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "".join(
                        getattr(part, "text", "") or ""
                        for part in content
                        if getattr(part, "text", None) is not None
                    )
                else:
                    msg_obj = getattr(event, "message", None)
                    if msg_obj is not None:
                        msg_content = getattr(msg_obj, "content", None)
                        if isinstance(msg_content, str):
                            text = msg_content
                if text and text.strip():
                    captured.append(text)
            except Exception as exc:  # noqa: BLE001 — capture must never raise
                logger.warning(
                    "openhands.capture_failed",
                    error=str(exc),
                    event_type=type(event).__name__,
                )

        llm = self._build_llm()
        # Tools list is intentionally empty for Phase 0 — no MCP, no
        # skills, no DelegateTool. Phase 1/2/3 add each surface.
        agent_kwargs: dict[str, Any] = {"llm": llm, "tools": []}
        if self._system_prompt:
            # The SDK's Agent constructor accepts a system prompt
            # field name that has shifted across pre-1.x revisions.
            # Try the documented name first and fall back to known
            # aliases — same pattern claude_code uses for option
            # construction.
            try:
                agent = self._agent_cls(system_prompt=self._system_prompt, **agent_kwargs)
            except TypeError:
                try:
                    agent = self._agent_cls(
                        system_message=self._system_prompt, **agent_kwargs
                    )
                except TypeError:
                    agent = self._agent_cls(**agent_kwargs)
        else:
            agent = self._agent_cls(**agent_kwargs)

        # Workspace pinned to the agent's materialized cwd
        # (~/.doorae/agents/<id>/), matching #345 / #349 runtime cwd
        # collapse. Conversation accepts ``str | Path | LocalWorkspace``
        # per the SDK API reference.
        try:
            conversation = self._conversation_cls(
                agent=agent,
                workspace=str(Path.cwd()),
                callbacks=[_capture_assistant],
            )
        except TypeError:
            # Older SDK signatures may use a different kwarg name for
            # the event callback list — fall back so Phase 0 keeps
            # booting against alternate revisions.
            conversation = self._conversation_cls(
                agent=agent,
                workspace=str(Path.cwd()),
            )
            # Best-effort post-attach. The SDK has no documented
            # ``add_callback`` helper at the time of writing; if this
            # fallback path is reached, capture stays empty and
            # ``on_message`` returns ``None`` — the same degraded mode
            # a missing SDK already produces.
            attach = getattr(conversation, "add_callback", None) or getattr(
                conversation, "subscribe", None
            )
            if attach is not None:
                try:
                    attach(_capture_assistant)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "openhands.callback_attach_failed",
                        error=str(exc),
                    )

        self._conversations[room_id] = (conversation, captured)
        return conversation, captured


async def integrate_with_openhands(
    client: ChatClient,
    agent_config: dict[str, Any] | None = None,
) -> OpenHandsAdapter:
    """Hook incoming messages to the OpenHands SDK.

    Mirrors ``integrate_with_claude_code`` so the new engine plugs in
    through ``RoomHandlerSupervisor`` (timeout / cycle guard /
    metrics) and respects the same 3-state policy gate (#74 /
    ``decide_policy``). Returns the adapter for lifecycle management.
    """
    config = agent_config or {}
    adapter = OpenHandsAdapter(
        agent_name=config.get("name", "OpenHands"),
        system_prompt=config.get("system_prompt"),
        model=config.get("model"),
        client=client,
        reasoning_effort=config.get("reasoning_effort"),
    )
    await adapter.start()

    engine_timeout = float(
        os.environ.get("DOORAE_AGENT_ENGINE_TIMEOUT_SEC", "900")
    )
    supervisor = RoomHandlerSupervisor(
        client=client, engine_name="openhands", engine_timeout=engine_timeout
    )

    @client.on_message
    async def _handle(msg: dict[str, Any]) -> None:
        room_id = msg.get("room_id", "")

        # 3-state policy gate. SKIP drops the message; INGEST_ONLY
        # buffers it for the next turn's prompt prefix; RESPOND
        # proceeds to the supervisor + engine path. Identical to the
        # other adapters — the gate logic stays adapter-agnostic so a
        # routing fix lands in one file (base.py).
        policy = decide_policy(msg, client)
        if policy is MessagePolicy.SKIP:
            return
        if policy is MessagePolicy.INGEST_ONLY:
            await adapter.ingest_context(msg)
            return

        # /delegate command — same pre-LLM hook other adapters honour.
        from doorae_agent.integrations.delegate import (
            execute_delegate,
            parse_delegate,
        )
        delegate = parse_delegate(msg.get("content", ""))
        if delegate:
            await execute_delegate(client, msg, delegate)
            return

        # Cross-room representative routing.
        from doorae_agent.integrations.room_query import (
            execute_room_query,
            parse_room_query,
        )
        rq = parse_room_query(msg)
        if rq:
            await execute_room_query(client, msg, rq)
            return

        request_id = (msg.get("metadata") or {}).get("request_id")

        async def run_engine() -> str:
            typing_active = True

            async def _typing_loop() -> None:
                while typing_active:
                    await client.sendTyping(room_id, True)
                    await asyncio.sleep(2)

            typing_task = asyncio.create_task(_typing_loop())
            try:
                response = await adapter.on_message(msg)
                return response or ""
            finally:
                typing_active = False
                typing_task.cancel()
                await client.sendTyping(room_id, False)

        await supervisor.dispatch(
            room_id=room_id,
            request_id=request_id,
            run_engine=run_engine,
        )

    return adapter
