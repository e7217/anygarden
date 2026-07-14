"""OpenHands V1 SDK integration (#355 Phase 0).

Bridges Anygarden chat messages to the in-process OpenHands ``Conversation``
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
import importlib
import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from anygarden_agent import secrets as agent_secrets
from anygarden_agent.client import ChatClient
from anygarden_agent.coordination.pending_context import (
    append_context_line,
    drain_context,
    format_context_line,
)
from anygarden_agent.integrations.base import (
    EngineAdapter,
    MessagePolicy,
    compose_session_context_suffix,
    decide_policy,
)
from anygarden_agent.runtime.handler_wrapper import (
    EngineError,
    EngineTimeoutError,
    EngineTurn,
    RoomHandlerSupervisor,
    is_transient_error,
)
from anygarden_agent.integrations._turn_timeout import (
    resolve_supervisor_timeout,
    resolve_turn_timeout,
)


logger = structlog.get_logger(__name__)


# Issue #483 — upper bound on a single OpenHands turn. ``conversation.run``
# is a synchronous SDK call dispatched onto a worker thread via
# ``asyncio.to_thread``; if the agent loop / LLM call hangs it would run
# forever, with the supervisor's 900s ``wait_for`` as the *only* defence.
# Worse, the supervisor only cancels the awaiting *coroutine* — the worker
# thread (and its in-flight LLM call) keeps running as a zombie. We wrap
# the await in ``asyncio.wait_for`` so a stuck turn surfaces as a timeout,
# and on timeout call ``conversation.pause()`` (thread-safe; takes effect
# at the next agent-step boundary) as best-effort cancellation to bound
# the zombie. Mirroring codex's 600s default keeps the adapter the *first*
# line to fire (strictly below the supervisor's 900s
# ``ANYGARDEN_AGENT_ENGINE_TIMEOUT_SEC``). A dedicated env knob tunes it
# independently of the shared supervisor deadline.
_OPENHANDS_TURN_TIMEOUT = resolve_turn_timeout("openhands")


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


# Issue #355 Phase 1 — same path the materializer writes for claude-code
# (``packages/cluster/anygarden/mcp_templates/merge.py:CLAUDE_SETTINGS_PATH``).
# Sharing the path lets a single materializer pass cover both engines;
# the file shape is FastMCP-compatible so OpenHands consumes it
# unchanged via ``Agent(mcp_config=...)``.
_MCP_MANIFEST_PATH = ".mcp.json"

# Issue #355 Phase 2 — skills directory the materializer populates per
# the agent_dir whitelist (``_ALLOWED_PREFIXES`` includes ``skills/``).
# Each entry is ``skills/<slug>/SKILL.md`` with a YAML frontmatter
# containing ``name`` and ``description``. Phase 2 ships *skill
# awareness* — the adapter enumerates available skills into the
# system prompt so the LLM knows they exist and can describe them
# when asked. Wrapping each skill as a full OpenHands ``Tool``
# (Action / Observation / Executor classes per the SDK custom-tools
# guide) is deferred to a follow-up because it needs runtime
# validation against the live SDK to catch the schema-shape changes
# this PR can't otherwise exercise.
_SKILLS_DIR_NAME = "skills"


__all__ = [
    "OpenHandsAdapter",
    "integrate_with_openhands",
]


class OpenHandsAdapter(EngineAdapter):
    """Adapter that bridges Anygarden messages to the OpenHands V1 SDK.

    Parallels the structure of ``ClaudeCodeAdapter`` / ``CodexCliAdapter``:

    - ``_pending_context`` per-room buffer for ``INGEST_ONLY`` messages
      (#74 / #286 contract).
    - Per-room ``Conversation`` instance dict so multi-turn state stays
      attached to the right room.
    - ``start`` lazy-imports the SDK so ``import anygarden_agent`` succeeds
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
        self._tool_cls: Any = None
        # Phase 3 — flips True on first ``start`` once DelegateTool is
        # registered with the SDK. ``_get_or_create_conversation`` reads
        # this to decide whether to add ``Tool(name="DelegateTool")`` to
        # the agent's tools list.
        self._delegate_tool_registered: bool = False
        # Names of runtime tools (TerminalTool / FileEditorTool /
        # TaskTrackerTool) successfully registered on first ``start``.
        # Without these the agent has only FinishTool + ThinkTool +
        # MCP, so any prompt needing shell or file work terminates
        # after a single text turn (the SDK's
        # ``_handle_content_response`` marks the conversation FINISHED
        # whenever the LLM returns plain content with no tool call).
        # Registration is best-effort and granular: a partially-
        # available ``openhands-tools`` install attaches whichever
        # tools imported successfully and skips the rest, mirroring
        # how the adapter degrades gracefully on a missing SDK.
        self._runtime_tool_names: list[str] = []
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

        Issue #355 Phase 3 — also imports ``Tool`` and registers
        ``DelegateTool`` so the LLM can spawn / delegate to sub-agents
        within its conversation thread. The registration is global to
        the SDK process (``register_tool`` populates a module-level
        registry) so we only do it once on first ``start``.
        """
        try:
            from openhands.sdk import (  # type: ignore[import-not-found]
                LLM,
                Agent,
                Conversation,
                Tool,
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
        self._tool_cls = Tool

        # Phase 3 — best-effort registration of the OpenHands
        # ``DelegateTool``. The package layout (``openhands.tools.
        # delegate.DelegateTool``) and ``register_tool(name, cls)``
        # API are described in the SDK's agent-delegation guide. If
        # either import fails (older SDK, optional install), the
        # adapter still boots — sub-agent delegation just isn't
        # available, mirroring how the three CLI engines handle
        # missing plugins.
        self._delegate_tool_registered = _try_register_delegate_tool()

        # Runtime tool bundle — TerminalTool, FileEditorTool,
        # TaskTrackerTool. Each tool registers independently so a
        # partially-available ``openhands-tools`` install still
        # contributes whatever it can. Browser tools and the
        # sub-agent ``TaskToolSet`` are intentionally skipped: the
        # browser dependency is heavy and rarely needed for chat
        # agents, and ``TaskToolSet`` overlaps with the existing
        # ``DelegateTool`` path.
        self._runtime_tool_names = _try_register_runtime_tools()

        logger.info(
            "openhands.initialized",
            model=self._model,
            delegate_tool=self._delegate_tool_registered,
            runtime_tools=self._runtime_tool_names,
        )

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

        # Steps 2-3: anygarden context plumbing. Identical pipeline to
        # claude_code/codex/gemini_cli — the helpers live on the base
        # class precisely so a new engine adapter inherits the
        # behaviour for free (#286, #293).
        metadata = msg.get("metadata")
        prompt = self.assemble_user_content(
            room_id,
            content,
            metadata if isinstance(metadata, dict) else None,
            sender_participant_id=msg.get("participant_id"),
        )
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
        # #433 — stash the full turn input (memory/roster + user content)
        # handed to the engine for the run_engine closure to surface.
        self._record_turn_input(room_id, prompt)

        try:
            conversation, captured = self._get_or_create_conversation(room_id)
        except Exception as exc:
            logger.error(
                "openhands.conversation_init_failed",
                room_id=room_id,
                error=str(exc),
            )
            # #422 — propagate so the supervisor surfaces outcome=failed
            # and notifies the user instead of swallowing into silence.
            raise EngineError(str(exc)) from exc

        # Steps 4-5: drive the agent. ``secrets_in_env`` covers the
        # SDK construction window; the actual LLM call happens inside
        # ``run`` so we keep the env populated for the duration.
        with agent_secrets.secrets_in_env(list(_OPENHANDS_SDK_ENV_KEYS)):
            try:
                await asyncio.to_thread(conversation.send_message, prompt)
                # #483 — bound the (otherwise unbounded) synchronous
                # ``run`` so a hung agent loop surfaces as a timeout
                # instead of leaning on the supervisor's blunter 900s
                # coroutine cancel. ``wait_for`` only cancels the
                # awaiting coroutine, not the worker thread, so we also
                # ask the conversation to ``pause()`` — best-effort
                # cancellation that stops the loop at the next
                # agent-step boundary, bounding the zombie thread.
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(conversation.run),
                        timeout=_OPENHANDS_TURN_TIMEOUT,
                    )
                except asyncio.TimeoutError as exc:
                    self._request_conversation_pause(room_id, conversation)
                    logger.error(
                        "openhands.timeout",
                        room_id=room_id,
                        timeout=_OPENHANDS_TURN_TIMEOUT,
                    )
                    # #422 — surface as a timeout so the supervisor records
                    # outcome=timeout and notifies the user.
                    raise EngineTimeoutError(
                        f"openhands turn exceeded {_OPENHANDS_TURN_TIMEOUT}s"
                    ) from exc
            except EngineError:
                # Already-classified failure (e.g. the timeout above) —
                # re-raise without re-wrapping into a generic EngineError.
                raise
            except Exception as exc:
                logger.error(
                    "openhands.run_failed",
                    room_id=room_id,
                    error=str(exc),
                )
                # #422 — propagate so the supervisor records failed +
                # notifies the user instead of returning None (silent).
                # #457 — the LLM call happens inside ``run``; classify a
                # 429/5xx / conn-reset here as transient for the opt-in retry.
                raise EngineError(
                    str(exc), transient=is_transient_error(str(exc))
                ) from exc

        # Step 6: drain captured assistant text. The capture closure
        # appends to a per-conversation list reset between turns.
        text_parts = list(captured)
        captured.clear()
        if not text_parts:
            return None
        return "\n\n".join(p.strip() for p in text_parts if p.strip()).strip() or None

    def _request_conversation_pause(self, room_id: str, conversation: Any) -> None:
        """Best-effort cancellation of a timed-out ``conversation.run``.

        #483 — ``asyncio.wait_for`` only cancels the awaiting coroutine,
        not the worker thread the synchronous ``run`` is executing on,
        so the in-flight agent loop / LLM call would otherwise keep
        running as a zombie. OpenHands' ``Conversation.pause`` is
        documented as callable from any thread and stops the loop at the
        next agent-step boundary (an in-flight LLM completion still has
        to finish first — full cancellation is an SDK limitation, hence
        *best-effort*). Guarded by ``getattr`` + broad ``except`` so an
        older SDK without ``pause``, or a pause that itself raises,
        never masks the ``EngineTimeoutError`` the caller is about to
        surface."""
        pause = getattr(conversation, "pause", None)
        if not callable(pause):
            logger.warning(
                "openhands.pause_unavailable",
                room_id=room_id,
                hint="SDK Conversation has no pause(); zombie run thread may persist",
            )
            return
        try:
            pause()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "openhands.pause_failed", room_id=room_id, error=str(exc)
            )

    async def ingest_context(self, msg: dict[str, Any]) -> None:
        """Stash a non-addressed message as context for the next turn.

        Identical pattern to the three CLI adapters: format the line,
        append to the per-room buffer, let ``assemble_user_content``
        drain on the next active turn (#74, #286).
        """
        room_id = msg.get("room_id") or "_default"
        line = format_context_line(msg, roster=self._room_roster(room_id))
        if line is None:
            return
        append_context_line(self._pending_context, room_id, line)

    def _format_context_line(self, msg: dict[str, Any]) -> str | None:
        """Back-compat wrapper around the shared helper.

        Matches ``ClaudeCodeAdapter._format_context_line`` so any test
        that introspects an adapter via this method name keeps
        working when retargeted at this engine.
        """
        return format_context_line(
            msg, roster=self._room_roster(msg.get("room_id") or "_default")
        )

    def _drain_pending_context(self, room_id: str) -> str:
        """Back-compat wrapper around the shared helper."""
        return drain_context(self._pending_context, room_id)

    def _compose_system_prompt(self) -> str | None:
        """Build the effective system prompt for a new Conversation.

        Combines the per-agent skills awareness block (Phase 2) with
        the caller-provided ``system_prompt``. Skills come first so
        the LLM has the capability inventory before any task-specific
        instructions narrow its focus. Either component may be empty;
        we return ``None`` only when both are absent so the
        construct-fallback can skip the kwarg entirely.
        """
        skills_block = _load_skills_summary(Path.cwd() / _SKILLS_DIR_NAME)
        if skills_block and self._system_prompt:
            return f"{skills_block}\n\n{self._system_prompt}"
        if skills_block:
            return skills_block
        return self._system_prompt

    def _build_llm(self) -> Any:
        """Construct the SDK ``LLM`` from adapter config.

        Model strings follow litellm convention with a provider prefix
        (``anthropic/claude-opus-4-7``, ``openai/gpt-5.4``,
        ``gemini/gemini-3-pro-preview``). The catalog is the source of
        truth; this adapter simply forwards what it receives.

        Issue #366 — credentials are passed *explicitly* rather than
        relying on env-var discovery.

        Why: ``openhands.sdk.llm.LLM`` is a Pydantic model whose
        ``api_key`` / ``base_url`` fields are frozen at construction
        time. The original adapter constructed the LLM with no
        credentials and counted on the ``secrets_in_env`` context
        manager around ``Conversation.run`` to populate
        ``OPENAI_API_KEY`` for litellm's env-discovery path. But the
        LLM is built *before* we enter that context (inside
        ``_get_or_create_conversation``), so the constructor sees an
        empty env, caches ``api_key=None``, and litellm trusts that
        explicit ``None`` over env fallback. Result: every gateway
        request landed at ``/api/v1/llm/v1/chat/completions`` with no
        Bearer token → 401. (See #366 issue body for the trace.)

        Reading from ``agent_secrets`` directly side-steps the env-
        timing window entirely. The values are the same ones
        ``secrets_in_env`` would have bridged; we just pass them as
        constructor args. ``secrets_in_env`` is still kept around the
        ``run()`` call as belt-and-suspenders for any litellm path
        that *does* read env at request time (Anthropic / Gemini
        provider routes, model-specific overrides, etc.).
        """
        if self._llm_cls is None:
            raise RuntimeError("OpenHands SDK not initialized; call start() first.")
        if not self._model:
            raise RuntimeError(
                "OpenHandsAdapter requires an explicit model string with "
                "provider prefix (e.g. 'anthropic/claude-opus-4-7')."
            )
        kwargs: dict[str, Any] = {"model": self._model}

        # Phase 0/1/4 wire the gateway through ``OPENAI_*`` env keys —
        # the anygarden proxy is OpenAI-compat regardless of upstream
        # provider, so the model id always starts with ``openai/``
        # when the route goes through the gateway. Reading those keys
        # from agent_secrets directly is sufficient for that path; if
        # an operator later wires Anthropic/Gemini direct routes,
        # ``ANTHROPIC_API_KEY`` / ``GEMINI_API_KEY`` would be read
        # here too. Until then, the stored values cover every model
        # the catalog currently advertises.
        api_key = agent_secrets.get("OPENAI_API_KEY")
        if api_key:
            kwargs["api_key"] = api_key
        base_url = agent_secrets.get("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url

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

            Issue #372 — schema corrected against the actual SDK
            structure. ``openhands.sdk.event.MessageEvent`` exposes:
              - ``source: SourceType`` ('agent' / 'user'), NOT ``role``
              - ``llm_message: Message``, NOT ``message`` / ``content``
            and ``Message`` carries ``role`` + ``content`` (a list of
            ``TextContent`` / ``ImageContent`` parts).

            Pre-#372 the capture matched ``event.role`` /
            ``event.content`` which never existed → captured stayed
            empty → ``on_message`` returned ``None`` → user saw
            no reply despite the LLM call returning 200 OK.
            """
            try:
                event_type = type(event).__name__
                # OpenHands V1 SDK terminates a turn either by emitting a
                # ``MessageEvent`` (model returned plain text content) OR
                # by calling the built-in ``finish`` tool, which surfaces
                # as ``ActionEvent`` carrying ``FinishAction(message=...)``
                # — that ``message`` field is the canonical user-facing
                # reply (``openhands.sdk.tool.builtins.finish`` says:
                # "Final message to send to the user."). Smaller models
                # (qwen, some Llamas) overwhelmingly take the finish-tool
                # path, so listening only for MessageEvent silently
                # drops every reply. Capture both shapes here.
                if event_type == "ActionEvent":
                    source = getattr(event, "source", None)
                    source_value = getattr(source, "value", source)
                    if source_value != "agent":
                        return
                    action_obj = getattr(event, "action", None)
                    if action_obj is None:
                        return
                    if type(action_obj).__name__ != "FinishAction":
                        return
                    msg = getattr(action_obj, "message", None)
                    if isinstance(msg, str) and msg.strip():
                        captured.append(msg)
                    return
                if event_type != "MessageEvent":
                    return
                # First gate: is this an agent-emitted event? OpenHands
                # uses ``SourceType`` (string-valued enum 'agent' /
                # 'user' / etc) to distinguish, NOT a per-message
                # ``role`` field.
                source = getattr(event, "source", None)
                # ``source`` is an enum-like; compare against both raw
                # string and ``.value`` attribute so this works whether
                # the SDK ships a plain string literal or a StrEnum.
                source_value = getattr(source, "value", source)
                if source_value != "agent":
                    return
                # Second gate: ``llm_message.role == "assistant"``.
                # Tool execution results show up as MessageEvent with
                # source='agent' too but role='tool', and we don't
                # want those in the user-facing reply.
                llm_message = getattr(event, "llm_message", None)
                if llm_message is None:
                    return
                if getattr(llm_message, "role", None) != "assistant":
                    return
                # Content is a list of pydantic content blocks
                # (``TextContent`` carrying ``.text``, ``ImageContent``
                # carrying ``.image_url``, etc). Collect text from the
                # ones that have it; skip the rest. Defensive against
                # future content kinds — anything without ``.text`` is
                # silently ignored.
                content = getattr(llm_message, "content", None) or []
                text = "".join(
                    getattr(part, "text", "") or ""
                    for part in content
                    if getattr(part, "text", None) is not None
                )
                if text and text.strip():
                    captured.append(text)
            except Exception as exc:  # noqa: BLE001 — capture must never raise
                logger.warning(
                    "openhands.capture_failed",
                    error=str(exc),
                    event_type=type(event).__name__,
                )

        llm = self._build_llm()
        # Issue #355 Phase 1 / #525 — load the materialized ``.mcp.json``
        # from agent root so both admin-attached and builtin MCP servers
        # reach the LLM. Empty / missing file → no MCP, same as a fresh
        # agent without any servers attached. openhands-sdk >=1.35 types
        # ``Agent.mcp_config`` as ``dict[str, MCPServer]`` — a server map,
        # NOT the FastMCP ``{"mcpServers": {...}}`` envelope our
        # ``.mcp.json`` stores. ``_manifest_to_mcp_config`` unwraps the
        # envelope and runs the SDK's own ``coerce_mcp_config`` before
        # handing it to ``Agent(mcp_config=...)``. See #525.
        mcp_config = _manifest_to_mcp_config(
            _load_mcp_manifest(Path.cwd() / _MCP_MANIFEST_PATH)
        )
        # Issue #355 Phase 3 — attach DelegateTool to the agent so the
        # LLM can spawn / delegate to sub-agents inside its conversation
        # thread. The tool was registered globally on first ``start``;
        # here we just reference it by name. MCP tools are discovered
        # automatically from ``mcp_config``; Phase 2 skills surface as
        # system-prompt awareness rather than Tools.
        tools: list[Any] = []
        if self._delegate_tool_registered and self._tool_cls is not None:
            try:
                tools.append(self._tool_cls(name="DelegateTool"))
            except Exception as exc:  # noqa: BLE001 — never crash on tool init
                logger.warning(
                    "openhands.delegate_tool_attach_failed",
                    error=str(exc),
                )
        if self._tool_cls is not None:
            for tool_name in self._runtime_tool_names:
                try:
                    tools.append(self._tool_cls(name=tool_name))
                except Exception as exc:  # noqa: BLE001 — never crash on tool init
                    logger.warning(
                        "openhands.runtime_tool_attach_failed",
                        tool_name=tool_name,
                        error=str(exc),
                    )
        agent_kwargs: dict[str, Any] = {"llm": llm, "tools": tools}
        if mcp_config is not None:
            agent_kwargs["mcp_config"] = mcp_config

        # Issue #355 Phase 2 — augment the system prompt with a
        # skills awareness block. The materializer drops SKILL.md
        # files under ``<agent_root>/skills/<slug>/`` (whitelisted by
        # ``machine.agent_dir``), so we read those at conversation
        # creation time. Skills + caller-provided system prompt
        # combine in that order: skills first (capability inventory),
        # then any task-specific system prompt the operator passed.
        effective_system_prompt = self._compose_system_prompt()
        # Construct the Agent with progressive fallbacks so a minor
        # SDK signature shift doesn't ground the adapter:
        #   1. Try with mcp_config (Phase 1 path).
        #   2. On TypeError, retry without — the adapter still boots
        #      with no MCP and we log the degradation explicitly.
        # System-prompt naming is then layered on top of whichever
        # kwargs path succeeded (claude_code uses the same pattern
        # for ClaudeAgentOptions).
        def _try_construct(extra: dict[str, Any]) -> Any:
            kwargs = {**agent_kwargs, **extra}
            if effective_system_prompt:
                try:
                    return self._agent_cls(
                        system_prompt=effective_system_prompt, **kwargs
                    )
                except TypeError:
                    try:
                        return self._agent_cls(
                            system_message=effective_system_prompt, **kwargs
                        )
                    except TypeError:
                        return self._agent_cls(**kwargs)
            return self._agent_cls(**kwargs)

        try:
            agent = _try_construct({})
        except (TypeError, ValidationError) as exc:
            # #525 — widen from ``TypeError`` only to also catch pydantic
            # ``ValidationError``. openhands-sdk >=1.35 rejects a
            # mis-shaped ``mcp_config`` with a ``ValidationError`` (not a
            # ``TypeError``), so without this the adapter hard-fails
            # before the LLM call instead of degrading to "no MCP".
            if "mcp_config" in agent_kwargs:
                logger.warning(
                    "openhands.mcp_config_rejected_by_sdk",
                    error=str(exc),
                )
                agent_kwargs.pop("mcp_config", None)
                agent = _try_construct({})
            else:
                raise

        # Workspace pinned to the agent's materialized cwd
        # (~/.anygarden/agents/<id>/), matching #345 / #349 runtime cwd
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


def _try_register_delegate_tool() -> bool:
    """Register OpenHands' built-in ``DelegateTool`` with the SDK.

    Per the SDK's agent-delegation guide:

        from openhands.tools.delegate import DelegateTool
        from openhands.sdk.tool import register_tool
        register_tool("DelegateTool", DelegateTool)

    Returns ``True`` when both imports + the ``register_tool`` call
    succeed and the LLM-facing tool name is ready to be used in
    ``Tool(name="DelegateTool")``. Returns ``False`` (and logs a
    structured warning) on any failure — older SDK builds may not
    ship the tool, or the registry API may have moved. The adapter
    then boots without sub-agent delegation, which is the same
    degraded path Phase 0 already supports for missing-SDK scenarios.

    Idempotent: ``register_tool`` is documented as upserting on the
    name key, so calling it twice (e.g. when two adapter instances
    coexist in tests) is safe.
    """
    try:
        from openhands.tools.delegate import (  # type: ignore[import-not-found]
            DelegateTool,
        )
        from openhands.sdk.tool import (  # type: ignore[import-not-found]
            register_tool,
        )
    except ImportError as exc:
        logger.warning(
            "openhands.delegate_tool_unavailable",
            hint="pip install openhands-sdk[tools] (or upgrade SDK)",
            error=str(exc),
        )
        return False
    try:
        register_tool("DelegateTool", DelegateTool)
    except Exception as exc:  # noqa: BLE001 — registry may raise typed
        logger.warning(
            "openhands.delegate_tool_register_failed",
            error=str(exc),
        )
        return False
    return True


# Each entry: ``(public tool name, dotted module path, class attribute)``.
# Names match the ``Tool.name`` constant the runtime classes expose
# (``TerminalTool.name == "execute_bash"`` etc.) — kept verbatim so
# ``Tool(name=...)`` lookups against the SDK registry succeed.
_RUNTIME_TOOL_SPECS: tuple[tuple[str, str, str], ...] = (
    ("TerminalTool", "openhands.tools.terminal", "TerminalTool"),
    ("FileEditorTool", "openhands.tools.file_editor", "FileEditorTool"),
    ("TaskTrackerTool", "openhands.tools.task_tracker", "TaskTrackerTool"),
)


def _try_register_runtime_tools() -> list[str]:
    """Register OpenHands' runtime tool bundle with the SDK.

    Returns the names of tools that registered successfully so the
    adapter can attach the matching ``Tool(name=...)`` references on
    each new ``Conversation``. Tools that fail to import (older or
    missing ``openhands-tools`` install) or fail to register (registry
    rejection) are skipped individually — partial registration is
    preferred over an all-or-nothing failure so a deployment with a
    quirky tool subset still gains the rest.

    Mirrors ``_try_register_delegate_tool`` in style: best-effort,
    structured-log on degradation, never raises.

    Background — without these tools the agent only sees ``FinishTool``
    + ``ThinkTool`` (the SDK builtins). Models routinely emit a plain
    text preamble when they want to "use" a missing capability ("I'll
    check the hostname…"); the SDK's response dispatcher then takes
    the content-response path and marks the conversation FINISHED
    after that single message. Adding TerminalTool / FileEditorTool /
    TaskTrackerTool gives the model a real path to satisfy those
    requests.
    """
    try:
        from openhands.sdk.tool import (  # type: ignore[import-not-found]
            register_tool,
        )
    except ImportError as exc:
        logger.warning(
            "openhands.runtime_tools_unavailable",
            hint="pip install openhands-sdk (or upgrade)",
            error=str(exc),
        )
        return []

    registered: list[str] = []
    for tool_name, module_path, attr in _RUNTIME_TOOL_SPECS:
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            logger.warning(
                "openhands.runtime_tool_unavailable",
                tool_name=tool_name,
                module=module_path,
                hint="pip install openhands-tools (or upgrade)",
                error=str(exc),
            )
            continue
        cls = getattr(module, attr, None)
        if cls is None:
            logger.warning(
                "openhands.runtime_tool_attribute_missing",
                tool_name=tool_name,
                module=module_path,
                attribute=attr,
            )
            continue
        try:
            register_tool(tool_name, cls)
        except Exception as exc:  # noqa: BLE001 — registry may raise typed
            logger.warning(
                "openhands.runtime_tool_register_failed",
                tool_name=tool_name,
                error=str(exc),
            )
            continue
        registered.append(tool_name)
    return registered


def _load_skills_summary(skills_dir: Path) -> str | None:
    """Enumerate per-agent skills as a system-prompt awareness block.

    Walks ``skills_dir`` (default: ``<cwd>/skills``), reads every
    ``<slug>/SKILL.md``, parses the YAML-ish frontmatter for ``name``
    and ``description``, and returns a single markdown block that the
    adapter prepends to the agent's system prompt:

        ## Available skills

        - **<name>** — <description>
        - **<other>** — ...

    Returns ``None`` when the directory is missing, empty, or every
    SKILL.md fails to parse — the agent then boots without the block,
    same as a fresh agent without skills.

    Why frontmatter-only and not the full body: the SKILL body is
    typically thousands of tokens (procedural guides). Loading every
    body into every system prompt would blow the context budget and
    duplicate content the LLM only needs when actually invoking the
    skill. Phase 2 surfaces *that the skill exists*; full skill
    body / argument routing is a follow-up that wraps each skill in
    an OpenHands ``ToolDefinition`` (Action / Observation / Executor)
    so the SDK can fetch the body on tool-call rather than at agent
    start.

    Frontmatter parser is intentionally tiny — we read everything
    between two ``---`` lines and extract ``key: value`` pairs by
    splitting on the first colon. Avoids a hard dependency on PyYAML
    (which anygarden_agent doesn't otherwise require) and matches what
    the existing skills in this repo actually use (one-line scalar
    fields, no nested structures).
    """
    try:
        if not skills_dir.is_dir():
            return None
    except OSError:
        return None
    entries: list[tuple[str, str]] = []
    try:
        children = sorted(skills_dir.iterdir())
    except OSError as exc:
        logger.warning("openhands.skills_iter_failed", error=str(exc))
        return None
    for child in children:
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            raw = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "openhands.skill_read_failed",
                path=str(skill_md),
                error=str(exc),
            )
            continue
        meta = _parse_skill_frontmatter(raw)
        name = meta.get("name") or child.name
        description = meta.get("description")
        if not description:
            # Skip skills without a description — listing a name with
            # nothing alongside it just wastes prompt tokens. The
            # frontmatter convention in this repo always includes one.
            logger.debug(
                "openhands.skill_missing_description",
                path=str(skill_md),
            )
            continue
        entries.append((str(name), str(description)))
    if not entries:
        return None
    lines = ["## Available skills", ""]
    for name, description in entries:
        # Single-line description so the block stays readable in a
        # system prompt; if a SKILL.md ever ships a multi-line
        # description, collapse to first line for the awareness block.
        first_line = description.strip().splitlines()[0].strip()
        lines.append(f"- **{name}** — {first_line}")
    return "\n".join(lines)


def _parse_skill_frontmatter(raw: str) -> dict[str, str]:
    """Extract ``key: value`` pairs from a SKILL.md YAML frontmatter.

    Tolerant minimal parser: returns an empty dict when the file has
    no frontmatter, the frontmatter is malformed, or no recognisable
    pairs are present. Matches the field shape the existing skills
    use (``name``, ``description`` as one-line scalars).
    """
    if not raw.startswith("---"):
        return {}
    # Split off the leading delimiter then find the closing one.
    body = raw[3:]
    end = body.find("\n---")
    if end < 0:
        return {}
    block = body[:end]
    pairs: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sep = stripped.find(":")
        if sep <= 0:
            continue
        key = stripped[:sep].strip()
        value = stripped[sep + 1 :].strip()
        # Trim wrapping quotes — frontmatter writers often wrap
        # description strings to embed colons.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            pairs[key] = value
    return pairs


def _load_mcp_manifest(path: Path) -> dict[str, Any] | None:
    """Read the materialized ``.mcp.json`` and return the FastMCP dict.

    Returns ``None`` when the file is missing, empty, or unparsable —
    the OpenHands ``Agent`` then boots with no MCP, which is exactly
    the same state a fresh agent without attached MCP servers ends up
    in. Returning ``None`` lets ``_get_or_create_conversation`` skip
    the ``mcp_config=`` kwarg entirely instead of passing an empty
    dict that some SDK versions might reject.

    The file shape is what the cluster's
    ``mcp_templates/merge.py`` writes for claude-code: an outer
    ``{"mcpServers": {<name>: {...}}}`` envelope. We hand that
    envelope through unchanged because OpenHands' FastMCP integration
    consumes the same shape per the SDK's MCP guide.

    Decoding errors are logged at warning so an admin debugging a
    stuck agent can correlate "no tools showing up" with "your
    manifest didn't parse" instead of seeing silence.
    """
    try:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("openhands.mcp_manifest_read_failed", error=str(exc))
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "openhands.mcp_manifest_parse_failed",
            error=str(exc),
            path=str(path),
        )
        return None
    if not isinstance(data, dict):
        logger.warning(
            "openhands.mcp_manifest_unexpected_shape",
            path=str(path),
            type=type(data).__name__,
        )
        return None
    # Only forward when there's at least one server. An empty
    # ``mcpServers`` map is functionally equivalent to "no MCP" but
    # passing it through risks a SDK warning, so collapse to None.
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return None
    return data


def _manifest_to_mcp_config(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalise the ``.mcp.json`` envelope into the shape ``Agent`` expects.

    ``_load_mcp_manifest`` returns the FastMCP ``{"mcpServers": {<name>:
    {...}}}`` envelope (what ``mcp_templates/merge.py`` writes for
    claude-code). openhands-sdk **>=1.35** types ``Agent.mcp_config`` as
    ``dict[str, MCPServer]`` — a *server map*, not the envelope. Passing the
    envelope makes the SDK read ``"mcpServers"`` as a server name and reject
    the nested ``anygarden`` entry with ``Extra inputs are not permitted``
    (#525).

    So we unwrap the ``mcpServers`` map and hand it to the SDK's own
    ``coerce_mcp_config`` (FastMCP → ``MCPServer`` normalisation), matching how
    the SDK loads config internally (``plugin/loader.py`` / ``skills/skill.py``
    both call ``coerce_mcp_config(config["mcpServers"])``).

    When ``coerce_mcp_config`` is unavailable — a pre-1.35 SDK, or the test
    fake that stubs ``openhands.sdk`` without a ``mcp.config`` submodule — we
    fall back to the raw unwrapped server map, which is already the correct
    ``dict[str, ...]`` shape for those SDKs. Returns ``None`` for an
    empty/absent manifest so the caller omits the ``mcp_config`` kwarg
    entirely.
    """
    if not raw:
        return None
    servers = raw.get("mcpServers", raw)
    if not isinstance(servers, dict) or not servers:
        return None
    try:
        from openhands.sdk.mcp.config import (  # type: ignore[import-not-found]
            coerce_mcp_config,
        )
    except ImportError:
        return servers
    return coerce_mcp_config(servers)


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

    engine_timeout = resolve_supervisor_timeout(_OPENHANDS_TURN_TIMEOUT)
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
        from anygarden_agent.integrations.delegate import (
            execute_delegate,
            parse_delegate,
        )
        delegate = parse_delegate(msg.get("content", ""))
        if delegate:
            await execute_delegate(client, msg, delegate)
            return

        # Cross-room representative routing.
        from anygarden_agent.integrations.room_query import (
            execute_room_query,
            parse_room_query,
        )
        rq = parse_room_query(msg)
        if rq:
            await execute_room_query(client, msg, rq)
            return

        request_id = (msg.get("metadata") or {}).get("request_id")

        async def run_engine() -> EngineTurn:
            typing_active = True

            async def _typing_loop() -> None:
                while typing_active:
                    await client.sendTyping(room_id, True)
                    await asyncio.sleep(2)

            typing_task = asyncio.create_task(_typing_loop())
            try:
                response = await adapter.on_message(msg)
                # #433 — pair the reply with the stashed turn input.
                return EngineTurn(response or "", adapter._take_turn_input(room_id))
            finally:
                typing_active = False
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
                await client.sendTyping(room_id, False)
                # #433 — drain the stash even when on_message raised, so a
                # failed turn never leaks/leaves a stale prompt. No-op on ok.
                adapter._take_turn_input(room_id)

        await supervisor.dispatch(
            room_id=room_id,
            request_id=request_id,
            run_engine=run_engine,
        )

    return adapter
