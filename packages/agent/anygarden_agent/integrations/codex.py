"""Codex integration — app-server based adapter using codex-python SDK.

Uses ``codex.Codex`` to maintain a long-lived app-server process.
Each room gets its own thread, so conversation context is natively
preserved without rebuilding prompt history every message.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import structlog

from anygarden_agent.client import ChatClient
from anygarden_agent.coordination.pending_context import (
    append_context_line,
    format_context_line,
)
from anygarden_agent.integrations.base import EngineAdapter
from anygarden_agent.runtime.handler_wrapper import (
    EngineError,
    EngineTimeoutError,
    RoomHandlerSupervisor,
)

logger = structlog.get_logger(__name__)


# Issue #309 — semantic permission tier → codex native dial mapping.
# Cluster-side ``Agent.permission_level`` is a user-facing abstraction
# that each engine adapter resolves into its own knobs. For codex the
# knobs are ``sandbox`` (``read-only`` / ``workspace-write`` /
# ``danger-full-access``) and ``approval_policy``. Keeping the table
# in one place keeps test coverage exhaustive and makes future codex
# SDK changes (e.g. a renamed approval mode) a single-edit fix.
_CODEX_TIER_FLAGS: dict[str, tuple[str, str]] = {
    "restricted": ("read-only", "untrusted"),
    "standard":   ("workspace-write", "never"),
    "trusted":    ("danger-full-access", "never"),
}


def _resolve_codex_flags(
    permission_level: str | None,
) -> tuple[str, str]:
    """Translate a ``permission_level`` tier into ``(sandbox, approval_policy)``.

    ``None`` falls back to ``standard`` so existing pre-#309 rows
    keep their hardcoded behaviour without a backfill migration.
    Anything else raises ``ValueError`` so a typo (e.g.
    ``"trustred"``) fails loud at adapter construction rather than
    silently downgrading the agent's privilege.
    """
    if permission_level is None:
        return _CODEX_TIER_FLAGS["standard"]
    try:
        return _CODEX_TIER_FLAGS[permission_level]
    except KeyError as exc:
        raise ValueError(
            f"unknown permission_level: {permission_level!r} — "
            f"expected one of {sorted(_CODEX_TIER_FLAGS)}"
        ) from exc


# Issue #190 — upper bound on a single codex turn. The SDK's
# ``thread.run_text`` otherwise waits forever on ``stream.wait()``,
# which serialises the room's WS receive loop in ``_handle`` and can
# lock a room on a stuck turn. 10 minutes matches the observed P95 of
# legitimate tool-heavy queries while still guaranteeing the room
# recovers if the SDK or the app-server hangs.
_CODEX_TURN_TIMEOUT = 600


def _codex_thread_cwd() -> Path:
    """Return the cwd that codex should use for its sandbox root.

    anygarden-agent itself is spawned from ``agent_root``. Standard codex
    still needs a narrower workspace-write root because codex 0.128
    exposes writable roots but not read-only path exceptions for
    managed files under that same root. The machine materializer creates
    ``workspace/`` only for codex agents; older or test layouts fall
    back to ``agent_root``.
    """
    workspace = Path.cwd() / "workspace"
    if workspace.is_dir():
        return workspace
    return Path.cwd()


# Guards ``_install_parse_notification_shim`` against double-wrapping
# when ``start()`` runs more than once in the same process (tests,
# reconnects, etc). A boolean flag is sufficient — once the patched
# function is installed into the SDK module it stays installed for
# the life of the interpreter.
_PARSE_NOTIFICATION_PATCHED = False


def _make_lenient_parse_notification(
    original: Callable[..., Any],
    generic_notification_cls: type,
    error_cls: type[BaseException],
) -> Callable[..., Any]:
    """Return a wrapper around codex SDK's ``parse_notification``.

    Issue #190 — the bundled codex-cli Rust binary emits notifications
    (e.g. ``item/completed`` with new ``ThreadItem`` variants) whose
    payload shape the Python SDK's pydantic union doesn't recognise.
    The SDK's strict check then raises ``AppServerProtocolError`` even
    when ``strict_protocol=False``, because the *method* is known —
    killing the whole turn mid-stream and losing the final text.

    The wrapper always calls the original in non-strict mode. If that
    still raises our specific error class, we salvage the frame by
    returning a ``GenericNotification`` with the raw ``method`` and
    ``params``. Truly malformed frames (non-string method, non-dict
    params) still raise — the goal is only to tolerate payload-shape
    drift, not to silently drop garbage.

    Exposed as a top-level factory (rather than a nested closure) so
    the logic can be unit-tested without patching codex internals.
    """

    def lenient(message: Any, *, strict: bool) -> Any:
        # We deliberately ignore ``strict`` from the caller: the SDK's
        # strict mode is exactly what surfaces the protocol drift we
        # want to mask. Forcing ``strict=False`` lets the original
        # fallback path handle unknown *methods* generically; the
        # except-block below handles known-method / unknown-payload.
        try:
            return original(message, strict=False)
        except error_cls:
            method = (message or {}).get("method") if isinstance(message, Mapping) else None
            params = (message or {}).get("params") if isinstance(message, Mapping) else None
            if isinstance(method, str) and (params is None or isinstance(params, Mapping)):
                logger.debug(
                    "codex.unknown_notification_tolerated",
                    method=method,
                    param_keys=list(params.keys())[:8] if isinstance(params, Mapping) else [],
                )
                return generic_notification_cls(
                    method=method,
                    params=dict(params) if params else None,
                )
            raise

    return lenient


def _install_parse_notification_shim() -> None:
    """Patch ``codex.app_server._protocol_helpers.parse_notification``.

    Issue #190 — idempotent: the module-level
    ``_PARSE_NOTIFICATION_PATCHED`` flag ensures we only wrap the
    original function once even if ``start()`` is called repeatedly.

    The shim is defensive about SDK shape changes: if any of the
    internal attributes disappear in a future release the function
    silently no-ops so the adapter still starts (the underlying bug
    would then resurface, but visibly — not as a startup crash).

    The codex SDK's ``_session`` module imports ``parse_notification``
    at import time via ``from codex.app_server._protocol_helpers
    import parse_notification``, which binds the *original* function
    into ``_session`` as a local name. Monkey-patching only the
    ``_protocol_helpers`` attribute therefore doesn't affect the
    call site that actually runs during turn processing. We patch
    both module namespaces so the notification read loop picks up
    the lenient wrapper.
    """
    global _PARSE_NOTIFICATION_PATCHED
    if _PARSE_NOTIFICATION_PATCHED:
        return
    try:
        from codex.app_server import _protocol_helpers as ph
        from codex.app_server import _session as session_mod
        from codex.app_server.errors import AppServerProtocolError
    except Exception as exc:
        # Non-fatal: the real bug won't be masked, but the adapter
        # still boots. Emit a single warning so an upstream SDK layout
        # change is visible in logs.
        logger.warning("codex.shim_import_failed", error=str(exc))
        return
    if not hasattr(ph, "parse_notification") or not hasattr(ph, "GenericNotification"):
        logger.warning(
            "codex.shim_missing_symbols",
            has_parse=hasattr(ph, "parse_notification"),
            has_generic=hasattr(ph, "GenericNotification"),
        )
        return

    lenient = _make_lenient_parse_notification(
        ph.parse_notification,
        ph.GenericNotification,
        AppServerProtocolError,
    )
    ph.parse_notification = lenient
    # Replace the local binding in ``_session`` that the read-loop
    # actually calls. Guarded against SDK refactors that inline or
    # rename the import.
    if hasattr(session_mod, "parse_notification"):
        session_mod.parse_notification = lenient
    _PARSE_NOTIFICATION_PATCHED = True
    logger.info("codex.parse_notification_shim_installed")


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
        permission_level: str | None = None,
        approval_policy: str | None = None,
    ) -> None:
        self._model = model or "gpt-5.5"
        self._system_prompt = system_prompt
        self._reasoning_effort = reasoning_effort
        # Issue #309 — when ``permission_level`` is set, derive the
        # native dials from the tier (overrides any explicit
        # ``sandbox`` / ``approval_policy`` so the cluster's
        # admin-set tier always wins). Direct callers that pass
        # ``sandbox`` / ``approval_policy`` and skip
        # ``permission_level`` keep the legacy signature unchanged.
        if permission_level is not None or approval_policy is None:
            resolved_sandbox, resolved_approval = _resolve_codex_flags(
                permission_level
            )
            self._sandbox = resolved_sandbox
            self._approval_policy = resolved_approval
        else:
            self._sandbox = sandbox
            self._approval_policy = approval_policy
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
        self._turn_options_cls: Any = None
        # #237 — owning client reference for the memory / ephemeral
        # suffix. Wired in ``integrate_with_codex``; tests that bypass
        # the integration factory leave it None and the suffix helper
        # degrades to an empty string.
        self._client: Any = None
        # Issue #293 — sha-tracked memory + roster injector. Codex
        # threads persist history natively, so identical blocks must
        # not be re-injected (pollutes the conversation with duplicate
        # text). When the upstream content *changes* (new shared file,
        # roster delta) the injector emits a delta-labelled re-emission
        # so the model treats the repeat as an update rather than a
        # duplicate paste. Pre-#293 codex held two separate sha dicts
        # (``_memory_injected`` / ``_roster_injected``); the helper now
        # owns both with the same semantics.
        from anygarden_agent.integrations.base import ShaTrackedInjector

        self._injector = ShaTrackedInjector()

    async def start(self) -> None:
        """Start the Codex client (spawns app-server internally)."""
        try:
            from codex import Codex
            from codex.options import ThreadStartOptions, TurnOptions
        except ImportError:
            logger.warning(
                "codex.sdk_not_found",
                hint="Install: pip install codex-python",
            )
            return

        self._codex = Codex()
        self._thread_options_cls = ThreadStartOptions
        self._turn_options_cls = TurnOptions

        # Issue #190 — install the parse_notification shim *after* the
        # SDK has been imported (so the target module definitely
        # exists) and only for adapters that actually booted the codex
        # client. Guarded against double-wrap by a module-level flag.
        _install_parse_notification_shim()

        logger.info("codex.client_started")

        # Log AGENTS.md presence for debugging. The machine spawner now
        # launches the agent process from the canonical agent root; codex
        # may still use a narrower SDK thread cwd for workspace-write.
        try:
            agents_md = _codex_thread_cwd() / "AGENTS.md"
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
                # ``sandbox=workspace-write`` stays so the agent can
                # write within its codex sandbox root but can't escape
                # to the host filesystem or rewrite materialized files
                # in the parent agent_root.
                #
                # When ``_thread_options_cls`` is None (real SDK not
                # installed, or tests that bypass start() setup) the
                # call degrades to the legacy signature so nothing
                # breaks hard.
                if self._thread_options_cls is not None:
                    thread = self._codex.start_thread(
                        options=self._thread_options_cls(
                            approval_policy=self._approval_policy,
                            sandbox=self._sandbox,
                            cwd=str(_codex_thread_cwd()),
                            model=self._model or None,
                        ),
                    )
                else:
                    thread = self._codex.start_thread()
                self._threads[room_id] = thread
                logger.info(
                    "codex.thread_created",
                    room_id=room_id,
                    approval_policy=self._approval_policy,
                    sandbox=self._sandbox,
                )

            # Issue #286 — drain + ``<room_conversation>`` wrap +
            # concat lives on ``EngineAdapter.assemble_user_content``
            # so all three session adapters share one pipeline; see
            # the base method's docstring for the full rationale.
            metadata = msg.get("metadata")
            turn_content = self.assemble_user_content(
                room_id,
                content,
                metadata if isinstance(metadata, dict) else None,
            )

            # Issue #237 / #255 / #279 / #293 — sha-tracked memory +
            # roster injection. Codex threads persist history, so
            # identical repeats pollute the conversation; a changed
            # block however must land with a delta label so the model
            # treats it as an update rather than a duplicate paste.
            # The injector centralises both halves; codex agents do
            # not host the orchestrator ``handoff_to`` MCP tool
            # (claude_code owns that wiring) so roster gating is
            # purely the ``collaborative`` flag.
            from anygarden_agent.integrations.base import compose_memory_suffix

            memory_suffix = compose_memory_suffix(self._client, room_id)
            client = self._client
            roster_suffix = ""
            if client is not None and client.is_collaborative(room_id):
                roster_suffix = client.compose_roster_suffix(
                    room_id, with_collaborative_hint=True
                )
            prefix = self._injector.apply(
                room_id,
                memory_suffix=memory_suffix,
                roster_suffix=roster_suffix,
                memory_label="[공유 자료 업데이트]",
                roster_label="[팀 구성 업데이트]",
            )
            if prefix:
                turn_content = f"{prefix}\n\n{turn_content}"

            # Issue #190 — bound the turn with an explicit timeout so
            # a stuck codex call doesn't freeze the room's WS receive
            # loop indefinitely. ``threading.Event`` implements
            # ``SupportsIsSet`` which the SDK's ``_SignalWatcher``
            # polls to interrupt the stream cleanly on abort.
            abort_signal = threading.Event()
            run_text_kwargs: dict[str, Any] = {"signal": abort_signal}
            if self._turn_options_cls is not None and (
                self._model or self._reasoning_effort
            ):
                run_text_kwargs["turn_options"] = self._turn_options_cls(
                    model=self._model or None,
                    effort=self._reasoning_effort or None,
                )
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        thread.run_text, turn_content, **run_text_kwargs
                    ),
                    timeout=_CODEX_TURN_TIMEOUT,
                )
            except asyncio.TimeoutError as exc:
                abort_signal.set()
                logger.error(
                    "codex.timeout",
                    room_id=room_id,
                    timeout=_CODEX_TURN_TIMEOUT,
                )
                # Drop the thread so the next message starts a fresh
                # turn rather than piling onto the aborted one.
                self._threads.pop(room_id, None)
                # #422 — surface as a timeout so the supervisor notifies
                # the user instead of swallowing it into a silent ok.
                raise EngineTimeoutError(
                    f"codex turn exceeded {_CODEX_TURN_TIMEOUT}s"
                ) from exc
            return response if response else None
        except EngineError:
            # already-classified engine failure (e.g. the timeout above)
            self._threads.pop(room_id, None)
            raise
        except Exception as exc:
            logger.error("codex.turn_failed", room_id=room_id, error=str(exc))
            # Remove broken thread so next message creates a fresh one
            self._threads.pop(room_id, None)
            # #422 — propagate so the supervisor records outcome=failed
            # and notifies the user, instead of returning None (silent).
            raise EngineError(str(exc)) from exc

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
    permission_level: str | None = None,
) -> CodexAdapter:
    """Hook incoming messages to the Codex app-server.

    The host machine must have `codex` installed and authenticated.
    Returns the adapter instance for lifecycle management.

    ``permission_level`` (#309) is the cluster-side semantic tier
    (``restricted``/``standard``/``trusted``). When ``None`` the
    spawner's ``ANYGARDEN_AGENT_PERMISSION_LEVEL`` env var is consulted
    so the CLI entry can stay tier-agnostic (the cluster pushes the
    value as env, the adapter resolves to native dials).
    """
    if permission_level is None:
        env_tier = os.environ.get("ANYGARDEN_AGENT_PERMISSION_LEVEL")
        if env_tier:
            permission_level = env_tier
    adapter = CodexAdapter(
        model=model,
        system_prompt=system_prompt,
        reasoning_effort=reasoning_effort,
        permission_level=permission_level,
    )
    # #237 — hook client reference so Codex can pull memory / ephemeral
    # suffix from the welcome-frame cache.
    adapter._client = client
    await adapter.start()

    engine_timeout = float(
        os.environ.get("ANYGARDEN_AGENT_ENGINE_TIMEOUT_SEC", "900")
    )
    supervisor = RoomHandlerSupervisor(
        client=client, engine_name="codex", engine_timeout=engine_timeout
    )

    @client.on_message
    async def _handle(msg: dict[str, Any]) -> None:
        room_id = msg.get("room_id", "")

        # 3-state gate (#74, #148). SKIP drops; INGEST_ONLY stashes
        # for next-turn prefix; RESPOND proceeds. The server decides
        # ambient candidacy via ``metadata.ingest_only`` (#148 Part
        # 3); the adapter just reacts to ``decide_policy``'s output.
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

        # #204 — route through the supervisor: one handler per room
        # (second concurrent dispatch is rejected), lifecycle events
        # emitted per phase, engine call wrapped in wait_for so a
        # hung subprocess can't loop forever. The typing loop stays
        # in the local closure so the "…typing" UX survives, but it
        # no longer drives the DB log — lifecycle events do.
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
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
                await client.sendTyping(room_id, False)

        await supervisor.dispatch(
            room_id=room_id,
            request_id=request_id,
            run_engine=run_engine,
        )

    return adapter
