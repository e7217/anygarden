"""Claude Code SDK integration.

Uses the Anthropic ``claude-agent-sdk`` Python library (previously
``claude-code-sdk``) to forward Anygarden chat messages to Claude Code
and stream back the response.

Per-agent configuration is carried in by Phase 0's materialized
directory layout. The anygarden-agent subprocess is spawned with cwd
set to ``~/.anygarden/agents/<id>/`` so this adapter can lean on
``Path.cwd()`` as the working directory. The materializer drops:

- ``AGENTS.md`` and ``CLAUDE.md`` (symlinked to ``AGENTS.md``)
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

import asyncio
from pathlib import Path
from typing import Any

import structlog

from anygarden_agent import secrets as agent_secrets
from anygarden_agent.client import ChatClient
from anygarden_agent.coordination.pending_context import (
    PENDING_CONTEXT_MAX as _PENDING_CONTEXT_MAX,
    PENDING_CONTEXT_TTL_SEC as _PENDING_CONTEXT_TTL_SEC,
    append_context_line,
    drain_context,
    format_context_line,
)
from anygarden_agent.integrations.base import EngineAdapter
from anygarden_agent.integrations.engine_session_store import (
    load_sessions,
    save_sessions,
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


# #197 — Anthropic-SDK env var names the claude-agent-sdk reads when
# discovering credentials. When the admin has configured anygarden's LLM
# gateway, the manifest carries per-agent values for these under
# ``engine_secrets``; we bridge them into ``os.environ`` only for the
# duration of the SDK call so a stray tool (Bash, Read) inside the
# agent can't read them off ``/proc/self/environ`` between turns.
_ANTHROPIC_SDK_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
)


# Issue #483 — upper bound on a single claude-agent-sdk turn. The SDK's
# ``query()`` async generator otherwise streams forever if the upstream
# model / tool loop hangs, which used to leave the supervisor's 900s
# ``wait_for`` as the *only* defence — 4-7.5x slower to notify than the
# codex (600s) / gemini (120s) adapters, which already self-time-out.
# Mirroring codex's 600s default keeps the adapter the *first* line to
# fire (it must stay strictly below the supervisor's
# ``ANYGARDEN_AGENT_ENGINE_TIMEOUT_SEC`` of 900s so the engine-side
# cancellation + room notice runs before the supervisor's blunter
# coroutine cancel). A dedicated env knob lets ops tune it without
# touching the shared supervisor deadline.
_CLAUDE_TURN_TIMEOUT = resolve_turn_timeout("claude")

logger = structlog.get_logger(__name__)

# Stage A test modules import ``_PENDING_CONTEXT_MAX`` /
# ``_PENDING_CONTEXT_TTL_SEC`` directly from this module. The symbols
# now live in ``coordination.pending_context``; re-exporting them
# here (and pinning ``__all__``) keeps those tests source-compatible
# without copying the constants.
__all__ = [
    "ClaudeCodeAdapter",
    "integrate_with_claude_code",
    "_PENDING_CONTEXT_MAX",
    "_PENDING_CONTEXT_TTL_SEC",
]


def _coerce_int(value: Any) -> int | None:
    """Best-effort int coercion for a usage counter (None on garbage)."""
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _extract_result_usage(message: Any) -> dict[str, Any] | None:
    """Normalise a claude-agent-sdk ``ResultMessage`` into a usage record.

    #461 (Wave 2d) — claude-code is the one CLI engine whose SDK reports
    token usage *and* a self-reported cost on its terminal
    ``ResultMessage``, so it is the confirmed coverage for gateway-free
    LLM telemetry. Returns a dict with ``model`` / ``input_tokens`` /
    ``output_tokens`` / ``cost_usd`` (any of which may be ``None``), or
    ``None`` when the message carries no usable usage signal at all.

    The SDK's ``usage`` field is a free-form ``dict[str, Any]`` carrying
    the raw Anthropic ``usage`` shape (``input_tokens`` / ``output_tokens``
    plus a cache breakdown); we prefer those keys and fall back to the
    OpenAI-style ``prompt_tokens`` / ``completion_tokens`` so a future SDK
    shape change degrades gracefully rather than dropping the row. The
    resolved model name is read from ``model_usage`` (a ``{model: …}``
    map) when present. ``total_cost_usd`` is the SDK's *self-reported*
    estimate — labelled as such, not a provider invoice.
    """
    usage = getattr(message, "usage", None)
    input_tokens: int | None = None
    output_tokens: int | None = None
    if isinstance(usage, dict):
        input_tokens = _coerce_int(
            usage.get("input_tokens")
            if usage.get("input_tokens") is not None
            else usage.get("prompt_tokens")
        )
        output_tokens = _coerce_int(
            usage.get("output_tokens")
            if usage.get("output_tokens") is not None
            else usage.get("completion_tokens")
        )

    cost_raw = getattr(message, "total_cost_usd", None)
    cost_usd = float(cost_raw) if isinstance(cost_raw, (int, float)) and not isinstance(cost_raw, bool) else None

    # ``model_usage`` is ``{model_name: {...per-model usage...}}``. The
    # first key is the resolved model label for the turn. Falls back to
    # None when absent so the cluster row still records counts/cost.
    model: str | None = None
    model_usage = getattr(message, "model_usage", None)
    if isinstance(model_usage, dict) and model_usage:
        first_key = next(iter(model_usage), None)
        if isinstance(first_key, str) and first_key:
            model = first_key

    if (
        model is None
        and input_tokens is None
        and output_tokens is None
        and cost_usd is None
    ):
        return None
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }


class ClaudeCodeAdapter(EngineAdapter):
    """Adapter that bridges Anygarden messages to the Claude Agent SDK."""

    def __init__(
        self,
        agent_name: str = "ClaudeCode",
        system_prompt: str | None = None,
        model: str | None = None,
        client: ChatClient | None = None,
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
        # Per-room buffer of context lines awaiting injection (#74).
        # Each entry is ``(monotonic_ts, line)``. ``ingest_context``
        # appends here; ``on_message`` pops the whole buffer into a
        # prompt prefix on the next active turn. The engine SDK's
        # own session history keeps what we inject thereafter, so a
        # line is meant to be consumed exactly once.
        self._pending_context: dict[str, list[tuple[float, str]]] = {}
        # Issue #159 Phase C — reference to the owning ChatClient so
        # the in-process ``handoff_to`` tool can emit ``[HANDOFF]``
        # markers back to the room. ``None`` preserves source-compat
        # for call sites that construct the adapter before
        # ``integrate_with_claude_code`` wires the client in.
        self._client = client
        # The ``handoff_to`` tool needs to know which room the
        # current LLM turn belongs to, but the SDK tool_use callback
        # fires after ``on_message`` has already returned. We stash
        # the room id on the instance for the duration of each turn
        # and clear it in ``finally`` so a later stray tool call
        # can't leak cross-room.
        self._current_room_id: str | None = None
        # Lazily-built MCP server config — built once after ``start``
        # imports the SDK. Kept as ``None`` when the SDK is missing
        # so ``_build_options`` can skip the handoff wiring entirely.
        self._handoff_server_config: Any = None
        # #461 (Wave 2d) — last turn's LLM usage harvested from the SDK's
        # ``ResultMessage`` (token counts + SDK-self-reported cost). Like
        # ``_last_session_id`` it is stashed per turn inside
        # ``_collect_reply`` and read back by the ``run_engine`` closure
        # immediately after ``on_message`` returns, so the supervisor can
        # surface it on the ``engine_call_finished`` frame and the cluster
        # can record a gateway-free usage row. ``None`` between turns.
        self._last_usage: dict[str, Any] | None = None

    async def start(self) -> None:
        """Import the claude-agent-sdk and cache the query hook."""
        try:
            import claude_agent_sdk  # type: ignore[import-not-found]
        except ImportError:
            logger.warning(
                "claude_code.not_installed",
                hint="pip install anygarden-agent[claude-code]",
            )
            self._sdk = None
            return

        self._sdk = claude_agent_sdk
        self._options_cls = claude_agent_sdk.ClaudeAgentOptions
        self._query_fn = claude_agent_sdk.query
        # #526 — restore per-room resume handles persisted by a prior
        # process so a respawned adapter can ``resume`` instead of starting
        # cold. Missing/corrupt store → empty map (fresh start).
        self._sessions = load_sessions(Path.cwd())
        logger.info(
            "claude_code.initialized",
            version=getattr(claude_agent_sdk, "__version__", "?"),
        )

        # Informational breadcrumb: does the materializer's
        # ``CLAUDE.md`` exist in the agent cwd? This is the signal that
        # per-agent instructions are wired.
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

        # Issue #286 — drain + ``<room_conversation>`` wrap +
        # concat is the standard pipeline shared by every session
        # adapter, so the work happens in
        # ``EngineAdapter.assemble_user_content``. Pre-#286 this
        # block inlined the three steps; promoting them to the base
        # means a future augmentation lands once and propagates to
        # all session adapters automatically. The result is
        # byte-identical to the inline pipeline (#284 contract).
        metadata = msg.get("metadata")
        prompt = self.assemble_user_content(
            room_id,
            content,
            metadata if isinstance(metadata, dict) else None,
            sender_participant_id=msg.get("participant_id"),
        )
        # #433 — stash the user-turn input handed to the engine (memory/
        # roster ride in the system prompt via options, not here).
        self._record_turn_input(room_id, prompt)

        # Issue #159 Phase C — expose the current room to the
        # ``handoff_to`` tool closure. Cleared in ``finally`` so a
        # delayed tool_use callback can't hijack a later turn.
        self._current_room_id = room_id
        try:
            options = self._build_options(room_id)
            reply = await self._collect_reply(prompt, options)
            return reply
        except EngineError:
            raise
        except Exception as exc:
            logger.error("claude_code.query_failed", error=str(exc))
            # #422 — propagate so the supervisor surfaces outcome=failed
            # and notifies the user instead of swallowing into silence.
            # #457 — classify conn-reset / upstream-5xx / 429 as transient.
            raise EngineError(
                str(exc), transient=is_transient_error(str(exc))
            ) from exc
        finally:
            self._current_room_id = None

    async def ingest_context(self, msg: dict[str, Any]) -> None:
        """Stash a non-addressed message as context for the next turn.

        Called by the handler when ``decide_policy`` returns
        ``INGEST_ONLY`` — a message carrying ``metadata.ingest_only
        =True``. Canonical producers are ``[취합 결과]`` (room_query
        representative) and server-side ambient stamping when the
        room has ``context_window_enabled=True`` (#148 Part 3).
        Dropped silently when the message has no renderable content.
        """
        room_id = msg.get("room_id") or "_default"
        line = format_context_line(msg, roster=self._room_roster(room_id))
        if line is None:
            return
        append_context_line(self._pending_context, room_id, line)

    def _format_context_line(self, msg: dict[str, Any]) -> str | None:
        """Back-compat wrapper around the shared helper.

        Stage A tests exercise this method name directly; Stage B
        keeps the wrapper so those assertions keep passing while
        the logic itself lives in ``coordination.pending_context``.
        """
        return format_context_line(
            msg, roster=self._room_roster(msg.get("room_id") or "_default")
        )

    def _drain_pending_context(self, room_id: str) -> str:
        """Back-compat wrapper around the shared helper. See
        ``_format_context_line`` note on method-name stability."""
        return drain_context(self._pending_context, room_id)

    async def stop(self) -> None:
        self._sessions.clear()
        self._pending_context.clear()
        self._sdk = None

    def _build_options(self, room_id: str) -> Any:
        """Construct ClaudeAgentOptions for a given room.

        Key flags:

        - ``cwd`` pinned at ``Path.cwd()`` so the Claude Agent SDK
          discovers the per-agent project directory directly.
        - ``setting_sources=["project"]`` so CLAUDE.md, project
          skills, and ``.claude/settings.json`` actually load. The
          default of ``None`` silently skips them — that's the
          single most common "why aren't my skills firing?"
          mistake in claude-agent-sdk.
        - ``permission_mode="bypassPermissions"`` (Issue #134) so
          MCP tool calls auto-approve. Headless agents have no
          human to click "allow" on an interactive approval
          prompt, so the default gate silently blocks every MCP
          server attached via the admin UI. This mirrors the
          trust model already used by gemini-cli (``--approval-mode
          yolo``) and codex (``approval_policy="never"``).
        - ``resume`` carries the per-room session id forward so
          follow-up messages stay in the same conversation.
        - ``mcp_servers`` / ``allowed_tools`` (#159 Phase C) ship
          the in-process ``handoff_to`` MCP server only when this
          agent is the orchestrator of ``room_id``. Exposing it
          universally would tempt the LLM to forge turn-order
          decisions in rooms where it has no standing.
        """
        kwargs: dict[str, Any] = {
            "cwd": str(Path.cwd()),
            "setting_sources": ["project"],
            "permission_mode": "bypassPermissions",
        }
        if self._system_prompt is not None:
            kwargs["system_prompt"] = self._system_prompt
        if self._model is not None:
            kwargs["model"] = self._model
        session_id = self._sessions.get(room_id)
        if session_id is not None:
            kwargs["resume"] = session_id

        is_orchestrator = self._is_orchestrator_of(room_id)
        if is_orchestrator:
            self._ensure_handoff_server_config()
            if self._handoff_server_config is not None:
                # Issue #319 — register the in-process server under
                # ``"handoff"`` rather than ``"anygarden"`` to avoid
                # colliding with the cluster's HTTP MCP server which
                # the spawner wrote into ``.mcp.json`` under that
                # exact name (see ``mcp_templates/merge.py``
                # ``anygarden_default_entry`` for ``claude-code``). Both
                # entries should reach the LLM:
                #   - ``mcp__handoff__handoff_to`` (in-process, this
                #     adapter): orchestrator-only turn-order tool.
                #   - ``mcp__anygarden__*`` (cluster HTTP, autoloaded
                #     from ``.mcp.json``): ``mark_task_status``,
                #     ``ack_mention``, ``send_message``,
                #     ``create_task``, etc.
                # Pre-#319 we passed ``mcp_servers={"anygarden": …}``
                # *and* ``allowed_tools=["mcp__anygarden__handoff_to"]``
                # which (a) shadowed the cluster HTTP anygarden entry
                # because the SDK's ``--mcp-config`` flag overrode
                # the same name from ``.mcp.json`` and (b) used a
                # single-element whitelist that blocked every other
                # cluster tool, so the LLM literally couldn't call
                # ``mark_task_status`` on its own task.
                kwargs["mcp_servers"] = {
                    "handoff": self._handoff_server_config
                }
                # ``allowed_tools`` is intentionally not set: the
                # spawner-written ``.mcp.json`` already lists the
                # cluster's anygarden HTTP MCP and any admin-attached
                # third-party MCPs (e.g. GitHub). Pinning a narrow
                # whitelist here would re-introduce the original
                # blockade for those entries. The SDK serialises an
                # empty whitelist as a missing ``--allowedTools``
                # flag, which the CLI treats as "trust the bypass
                # permission_mode" — already in force above.

        # Issue #237 / #279 / #293 — append the centralised memory
        # + roster suffix. The roster gate fires either for the
        # orchestrator (handoff_to MCP path, no peer-mention hint)
        # or for a collaborative agent (mention-based delegation,
        # hint included). Done AFTER the base ``system_prompt`` so
        # AGENTS.md-derived personality still drives behaviour and
        # the suffix acts as an override at the end.
        from anygarden_agent.integrations.base import (
            compose_session_context_suffix,
        )

        client = self._client
        is_collab = client is not None and client.is_collaborative(room_id)
        suffix = compose_session_context_suffix(
            client,
            room_id,
            include_roster=is_orchestrator or is_collab,
            with_collaborative_hint=is_collab,
        )
        if suffix:
            existing = kwargs.get("system_prompt")
            kwargs["system_prompt"] = (
                f"{existing}\n\n{suffix}" if existing else suffix
            )

        return self._options_cls(**kwargs)

    def _is_orchestrator_of(self, room_id: str) -> bool:
        """Check whether the owning client is the room's orchestrator.

        Reads the client's ``_orchestrator_agent_id`` cache populated
        on every welcome frame (see ``ChatClient`` in client.py).
        Returns ``False`` when any link in the chain is missing so a
        partially-initialised client never accidentally ships the
        tool.
        """
        client = self._client
        if client is None:
            return False
        my_agent_id = getattr(client, "_agent_id", None)
        if not my_agent_id:
            return False
        orc_map = getattr(client, "_orchestrator_agent_id", None)
        if not isinstance(orc_map, dict):
            return False
        return orc_map.get(room_id) == my_agent_id

    def _ensure_handoff_server_config(self) -> None:
        """Build the in-process MCP server config on first use.

        The Claude Agent SDK ships ``tool`` / ``create_sdk_mcp_server``
        only when the package is installed. Guarding behind
        ``self._sdk`` keeps fallback paths (no SDK, monkeypatched
        module) honest — absence of either helper leaves the config
        as ``None`` and ``_build_options`` never stamps the server.
        """
        if self._handoff_server_config is not None:
            return
        sdk = self._sdk
        if sdk is None:
            return
        tool_fn = getattr(sdk, "tool", None)
        create_server_fn = getattr(sdk, "create_sdk_mcp_server", None)
        if tool_fn is None or create_server_fn is None:
            return

        @tool_fn(
            "handoff_to",
            (
                "Transfer the conversation to another room participant. "
                "Use this when another participant is better suited to "
                "respond next. The server converts the tool call into a "
                "[HANDOFF] message with an <@user:{participant_id}> "
                "mention; that participant will then take the next turn."
            ),
            {"participant_id": str, "reason": str},
        )
        async def _handoff_to(args: dict[str, Any]) -> dict[str, Any]:
            target_pid = args.get("participant_id") if isinstance(args, dict) else None
            reason = args.get("reason", "") if isinstance(args, dict) else ""
            client = self._client
            room_id = self._current_room_id
            if (
                not target_pid
                or not isinstance(target_pid, str)
                or client is None
                or room_id is None
            ):
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "handoff_to failed: missing participant_id or room context",
                        }
                    ],
                    "is_error": True,
                }
            reason_str = reason if isinstance(reason, str) else ""
            marker = f"[HANDOFF] <@user:{target_pid}> {reason_str}".rstrip()
            await client.send(
                room_id,
                marker,
                metadata={
                    "handoff": {
                        "target_participant_id": target_pid,
                        "reason": reason_str,
                    }
                },
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Handed off to {target_pid}.",
                    }
                ]
            }

        self._handoff_server_config = create_server_fn(
            name="anygarden",
            tools=[_handoff_to],
        )

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
        # #461 — usage harvested from the terminal ``ResultMessage``.
        usage_record: dict[str, Any] | None = None

        # #197 — Place the gateway env vars in ``os.environ`` only for
        # the duration of the SDK call. The claude-agent-sdk reads
        # ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` /
        # ``ANTHROPIC_API_KEY`` from the environment when it constructs
        # its HTTP client; outside this context manager they stay in
        # the private ``agent_secrets`` module so tool invocations
        # (Bash, Read) can't exfiltrate them via ``/proc/self/environ``.
        # If ``engine_secrets`` carried no such keys (operator hasn't
        # enabled the gateway for this agent), ``secrets_in_env`` is a
        # no-op and the SDK falls through to its default env / Bedrock
        # / Vertex discovery as before.
        # #483 — the SDK's ``query()`` is an async generator with no
        # intrinsic deadline; a hung upstream model / tool loop would
        # stream forever. We drain it inside an inner coroutine wrapped
        # in ``asyncio.wait_for`` so a stuck turn surfaces as a timeout
        # rather than wedging the room until the supervisor's blunter
        # 900s cancel. The generator handle is held explicitly so the
        # timeout path can ``aclose()`` it — without that the SDK's
        # transport / subprocess leaks. Symmetric with codex
        # (``abort_signal``) / gemini (``_terminate_tree``).
        agen = self._query_fn(prompt=prompt, options=options)

        async def _consume() -> None:
            nonlocal session_id, result_field, usage_record
            async for message in agen:
                msg_type = type(message).__name__

                sid = getattr(message, "session_id", None)
                if sid is not None:
                    session_id = sid

                # Only harvest text from AssistantMessage content blocks,
                # and only from TextBlock (skip tool use/result/thinking).
                if msg_type == "AssistantMessage":
                    content = getattr(message, "content", None) or []
                    for block in content:
                        block_type = type(block).__name__
                        # Issue #144 — observability: emit which tools
                        # Claude actually invokes so MCP wiring issues are
                        # diagnosable from structlog alone. ``input`` keys
                        # only (no values) because MCP tool arguments
                        # routinely carry secrets / PII (tokens, emails,
                        # repo names) — a full dump would leak credentials
                        # into log aggregators. Key names are enough to
                        # confirm the call happened and the shape was
                        # correct.
                        if block_type == "ToolUseBlock":
                            logger.info(
                                "claude_code.tool_use",
                                tool_name=getattr(block, "name", None),
                                input_keys=list(
                                    (getattr(block, "input", None) or {}).keys()
                                ),
                            )
                            continue
                        if block_type != "TextBlock":
                            continue
                        text = getattr(block, "text", None)
                        if isinstance(text, str) and text.strip():
                            text_parts.append(text)

                elif msg_type == "ResultMessage":
                    result = getattr(message, "result", None)
                    if isinstance(result, str) and result.strip():
                        result_field = result
                    # #461 (Wave 2d) — harvest token usage + SDK cost from
                    # the SDK's terminal ResultMessage. ``usage`` is the
                    # raw Anthropic ``usage`` dict (``input_tokens`` /
                    # ``output_tokens`` plus cache breakdown); ``model_usage``
                    # maps a model name → its per-model usage and gives us
                    # the resolved model label; ``total_cost_usd`` is the
                    # SDK's *self-reported* turn cost — an estimate, not a
                    # provider invoice. Captured into a normalised record so
                    # the run_engine closure can stamp it onto the EngineTurn.
                    usage_record = _extract_result_usage(message)

        with agent_secrets.secrets_in_env(list(_ANTHROPIC_SDK_ENV_KEYS)):
            try:
                await asyncio.wait_for(
                    _consume(), timeout=_CLAUDE_TURN_TIMEOUT
                )
            except asyncio.TimeoutError as exc:
                logger.error(
                    "claude_code.timeout", timeout=_CLAUDE_TURN_TIMEOUT
                )
                # #422 — surface as a timeout so the supervisor records
                # outcome=timeout and notifies the user, instead of a
                # swallowed silent None.
                raise EngineTimeoutError(
                    f"claude turn exceeded {_CLAUDE_TURN_TIMEOUT}s"
                ) from exc
            finally:
                # Always drain the generator's resources (transport /
                # subprocess). ``aclose()`` drives the ``query()`` body's
                # ``GeneratorExit`` cleanup; harmless if already exhausted.
                await agen.aclose()

        if session_id is not None:
            # Caller (integrate_with_claude_code) promotes this
            # into the per-room session map. Store on the instance
            # so the handler wrapper can grab it without having to
            # return session state alongside the reply.
            self._last_session_id = session_id

        # #461 — stash the harvested usage on the instance (mirrors
        # ``_last_session_id``); the run_engine closure reads it back via
        # ``_take_last_usage`` right after ``on_message`` returns. ``None``
        # when the SDK emitted no ResultMessage / no usage fields.
        self._last_usage = usage_record

        # ResultMessage.result is the SDK's definitive final reply
        # for the turn — prefer it when present. Fall back to the
        # concatenated assistant TextBlocks only if the SDK didn't
        # emit a ResultMessage (some streaming subtypes).
        if result_field:
            return result_field.strip()
        if text_parts:
            return "\n\n".join(part.strip() for part in text_parts).strip()
        return None

    def _take_last_usage(self) -> dict[str, Any] | None:
        """Pop the usage record harvested during the last ``_collect_reply``.

        #461 — read by the ``run_engine`` closure right after
        ``on_message`` returns so the harvested counts/cost are surfaced
        on the EngineTurn exactly once and never leak into a later turn.
        """
        usage = self._last_usage
        self._last_usage = None
        return usage


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
        # #159 Phase C — wire the adapter to its owning client so
        # the ``handoff_to`` tool closure can emit ``[HANDOFF]``
        # markers when the orchestrator invokes it.
        client=client,
    )
    await adapter.start()

    engine_timeout = resolve_supervisor_timeout(_CLAUDE_TURN_TIMEOUT)
    supervisor = RoomHandlerSupervisor(
        client=client, engine_name="claude-code", engine_timeout=engine_timeout
    )

    @client.on_message
    async def _handle(msg: dict[str, Any]) -> None:
        room_id = msg.get("room_id", "")

        # 3-state gate (#74). SKIP drops the message; INGEST_ONLY
        # stashes it for the next active turn's prompt prefix;
        # RESPOND proceeds to the full LLM flow below. The canonical
        # INGEST_ONLY case is a ``[취합 결과]`` broadcast flagged
        # with ``metadata.ingest_only=True`` — listeners absorb it
        # as room context instead of silently dropping it.
        from anygarden_agent.integrations.base import MessagePolicy, decide_policy
        policy = decide_policy(msg, client)
        if policy is MessagePolicy.SKIP:
            return
        if policy is MessagePolicy.INGEST_ONLY:
            await adapter.ingest_context(msg)
            return

        # Check for /delegate command before LLM call
        from anygarden_agent.integrations.delegate import parse_delegate, execute_delegate
        delegate = parse_delegate(msg.get("content", ""))
        if delegate:
            await execute_delegate(client, msg, delegate)
            return

        # Check for room_query (representative agent routing)
        from anygarden_agent.integrations.room_query import parse_room_query, execute_room_query
        rq = parse_room_query(msg)
        if rq:
            await execute_room_query(client, msg, rq)
            return

        # #204 — supervisor-routed path; see codex.py for rationale.
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
                # Promote the last session id captured during query back
                # into the per-room session map, so the next turn can
                # resume the conversation.
                sid = getattr(adapter, "_last_session_id", None)
                if sid is not None and room_id:
                    adapter._sessions[room_id] = sid
                    adapter._last_session_id = None
                    # #526 — persist so a future respawn can restore it.
                    save_sessions(Path.cwd(), adapter._sessions)
                # #433 — pair the reply with the stashed turn input.
                # #461 — also surface the harvested LLM usage so the cluster
                # records a gateway-free ``LLMGatewayUsage`` row. ``cost_usd``
                # is the SDK's self-reported estimate.
                usage = adapter._take_last_usage() or {}
                return EngineTurn(
                    response or "",
                    adapter._take_turn_input(room_id),
                    model=usage.get("model"),
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    cost_usd=usage.get("cost_usd"),
                )
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
                # #461 — likewise drain any usage stash a raised turn left so
                # it can't bleed into the next turn's frame.
                adapter._take_last_usage()

        await supervisor.dispatch(
            room_id=room_id,
            request_id=request_id,
            run_engine=run_engine,
        )

    return adapter
