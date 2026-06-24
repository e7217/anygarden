"""Codex CLI integration — subprocess adapter using ``codex exec --json``.

Unlike the SDK-based ``codex`` engine (``integrations/codex.py``), this
adapter shells out to the host ``codex`` binary's ``codex exec`` command
and parses its JSONL event stream. The binary is managed externally (on
PATH), so the engine is **decoupled from the codex-python SDK's bundled
binary version** — the root cause of the 2026-06-24 gpt-5.5 outage where
an old vendored binary rejected the model. See #496 and
``docs/paperclip-vs-anygarden-agent-integration.md`` (proposal 5).

Why a separate engine instead of replacing ``codex``:

- Zero risk to running SDK-codex agents; the two coexist and can be
  A/B compared (engine name ``codex-cli`` vs ``codex``).
- JSONL is a stable wire contract, so the ``parse_notification``
  monkeypatch shim (#190) the SDK needs is unnecessary here — unknown
  event ``type``s are simply skipped.

Session continuity uses codex's native ``resume``: the first turn in a
room captures ``thread.started.thread_id`` from the JSONL stream and
later turns pass it to ``codex exec resume <id>`` so conversation history
is preserved by codex itself (no per-turn transcript rebuild). A resume
against an expired/missing session is retried once as a fresh turn.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import structlog

from anygarden_agent.client import ChatClient
from anygarden_agent.coordination.pending_context import (
    append_context_line,
    format_context_line,
)
from anygarden_agent.integrations.base import EngineAdapter, ShaTrackedInjector
from anygarden_agent.integrations.codex import _codex_thread_cwd, _resolve_codex_flags
from anygarden_agent.integrations.gemini_cli import (
    _subprocess_group_kwargs,
    _terminate_tree,
)
from anygarden_agent.integrations._turn_timeout import (
    resolve_supervisor_timeout,
    resolve_turn_timeout,
)
from anygarden_agent.runtime.handler_wrapper import (
    EngineError,
    EngineTimeoutError,
    EngineTurn,
    RoomHandlerSupervisor,
    is_transient_error,
)

logger = structlog.get_logger(__name__)


# Same turn-timeout profile as the SDK codex engine (#190/#492): codex
# tool turns can reason for minutes. Shares the ``codex`` env override
# key (``ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC``) since the underlying
# binary and turn profile are identical.
_CODEX_CLI_TIMEOUT = resolve_turn_timeout("codex")


def _resolve_codex_cli_args(permission_level: str | None) -> list[str]:
    """Translate a permission tier into ``codex exec`` CLI flags.

    Reuses the SDK adapter's ``_CODEX_TIER_FLAGS`` table (via
    ``_resolve_codex_flags``) so both codex engines share one permission
    model: ``restricted`` → ``read-only``/``untrusted``, ``standard`` →
    ``workspace-write``/``never``, ``trusted`` → ``danger-full-access``/
    ``never``. ``-s`` accepts the sandbox mode directly; the approval
    policy rides ``-c approval_policy=<p>`` (both verified against codex
    exec 0.140/0.141: sandbox values read-only|workspace-write|
    danger-full-access; approval_policy untrusted|never|...). An unknown
    tier raises ``ValueError`` (fail-loud, same as the SDK adapter).
    """
    sandbox, approval_policy = _resolve_codex_flags(permission_level)
    return ["-s", sandbox, "-c", f"approval_policy={approval_policy}"]


class CodexCliAdapter(EngineAdapter):
    """Adapter that calls the host ``codex exec`` CLI via subprocess.

    The host machine must have ``codex`` installed and authenticated
    (ChatGPT login under ``~/.codex/auth.json`` or the machine's
    ``.codex`` overlay), exactly like the SDK ``codex`` engine.
    """

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str = "You are a helpful team member in a multi-agent chat. Answer concisely.",
        reasoning_effort: str | None = None,
        permission_level: str | None = None,
    ) -> None:
        self._model = model or "gpt-5.5"
        self._system_prompt = system_prompt
        self._reasoning_effort = reasoning_effort
        self._permission_level = permission_level
        self._codex_path: str | None = None
        # Per-room codex session id (``thread.started.thread_id``) for
        # ``codex exec resume <id>``. Absent → next turn starts fresh.
        self._room_thread_ids: dict[str, str] = {}
        # Per-room pending context buffer (#74 Stage B) — INGEST_ONLY
        # breadcrumbs drained as the next active turn's prompt prefix.
        self._pending_context: dict[str, list[tuple[float, str]]] = {}
        # #237 — owning client for memory/roster suffix composition.
        self._client: ChatClient | None = None
        # #461 — last turn's usage parsed from ``turn.completed``.
        self._last_usage: dict[str, Any] | None = None
        # #293 — sha-tracked memory/roster injector. resume preserves
        # history natively, so re-injecting identical blocks would
        # pollute the conversation; only changed blocks re-emit.
        self._injector = ShaTrackedInjector()

    async def start(self) -> None:
        """Verify the ``codex`` binary is installed and reachable."""
        self._codex_path = shutil.which("codex")
        if self._codex_path:
            logger.info("codex_cli.found", path=self._codex_path)
        else:
            logger.warning(
                "codex_cli.not_found",
                hint="Install codex: npm i -g @openai/codex",
            )

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Forward the message to ``codex exec`` and return the reply."""
        if self._codex_path is None:
            return None

        content = msg.get("content", "")
        if not content:
            return None

        room_id = msg.get("room_id", "_default")

        # Issue #286 — shared drain → ``<room_conversation>`` wrap →
        # concat pipeline (same as codex/gemini/claude adapters).
        metadata = msg.get("metadata")
        turn_content = self.assemble_user_content(
            room_id,
            content,
            metadata if isinstance(metadata, dict) else None,
        )

        # Issue #237/#293 — sha-tracked memory + roster injection. codex
        # resume preserves history, so identical repeats are suppressed;
        # a changed block re-emits with a delta label.
        from anygarden_agent.integrations.base import compose_memory_suffix

        memory_suffix = compose_memory_suffix(self._client, room_id)
        roster_suffix = ""
        client = self._client
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

        # #433 — stash the exact text handed to the engine.
        self._record_turn_input(room_id, turn_content)

        try:
            response = await self._call_codex(turn_content, room_id)
            return response if response else None
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("codex_cli.turn_failed", room_id=room_id, error=str(exc))
            raise EngineError(
                str(exc), transient=is_transient_error(str(exc))
            ) from exc

    async def _call_codex(self, prompt: str, room_id: str) -> str | None:
        """Run one ``codex exec`` turn (resume when a session exists).

        Returns the agent reply text (``-o`` last-message file, falling
        back to concatenated ``agent_message`` items) or ``None``. On a
        resume against a vanished session the room's thread id is dropped
        and the turn is retried once as a fresh session.
        """
        thread_id = self._room_thread_ids.get(room_id)
        response, new_thread_id, usage, resume_failed = await self._exec_once(
            prompt, thread_id
        )
        if resume_failed and thread_id is not None:
            # Session expired/missing — drop it and retry fresh once.
            logger.info("codex_cli.resume_failed_retry", room_id=room_id)
            self._room_thread_ids.pop(room_id, None)
            response, new_thread_id, usage, _ = await self._exec_once(prompt, None)

        if new_thread_id:
            self._room_thread_ids[room_id] = new_thread_id
        self._last_usage = self._extract_usage(usage, self._model)
        return response

    async def _exec_once(
        self, prompt: str, thread_id: str | None
    ) -> tuple[str | None, str | None, dict[str, Any] | None, bool]:
        """One subprocess invocation. Returns (reply, thread_id, usage, resume_failed)."""
        agent_root = _codex_thread_cwd()
        # ``-o`` writes the final agent message to a file — the most
        # robust way to recover the reply regardless of JSONL drift.
        fd, last_msg_path = tempfile.mkstemp(prefix="codex-cli-", suffix=".txt")
        os.close(fd)
        try:
            cmd: list[str] = [self._codex_path or "codex", "exec"]
            if thread_id:
                cmd += ["resume", thread_id]
            cmd += ["--json", "--skip-git-repo-check", "-C", str(agent_root)]
            cmd += _resolve_codex_cli_args(self._permission_level)
            if self._model:
                cmd += ["-m", self._model]
            if self._reasoning_effort:
                cmd += ["-c", f"model_reasoning_effort={self._reasoning_effort}"]
            cmd += ["-o", last_msg_path, "-"]  # prompt via stdin

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(agent_root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_subprocess_group_kwargs(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=prompt.encode()),
                    timeout=_CODEX_CLI_TIMEOUT,
                )
            except asyncio.TimeoutError as exc:
                await asyncio.to_thread(_terminate_tree, proc.pid, 5.0)
                await proc.wait()
                logger.error("codex_cli.timeout", timeout=_CODEX_CLI_TIMEOUT)
                raise EngineTimeoutError(
                    f"codex-cli turn exceeded {_CODEX_CLI_TIMEOUT}s"
                ) from exc

            raw = stdout.decode(errors="replace")
            parsed_thread_id, jsonl_text, usage = self._parse_codex_jsonl(raw)

            if proc.returncode != 0:
                stderr_snippet = stderr.decode(errors="replace")[:500]
                # A resume against a vanished session fails non-zero; let
                # the caller retry fresh rather than surfacing an error.
                resume_failed = thread_id is not None
                if not resume_failed:
                    logger.error(
                        "codex_cli.nonzero_exit",
                        code=proc.returncode,
                        stderr=stderr_snippet,
                    )
                    raise EngineError(
                        f"codex-cli exited with code {proc.returncode}"
                        + (f": {stderr_snippet}" if stderr_snippet.strip() else ""),
                        transient=is_transient_error(stderr_snippet),
                    )
                return None, None, None, True

            # Prefer the ``-o`` file; fall back to JSONL agent_message.
            file_text = ""
            try:
                file_text = Path(last_msg_path).read_text(errors="replace").strip()
            except OSError:
                pass
            response = file_text or jsonl_text
            return (response or None), parsed_thread_id, usage, False
        finally:
            try:
                os.unlink(last_msg_path)
            except OSError:
                pass

    @staticmethod
    def _parse_codex_jsonl(
        raw: str,
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        """Parse ``codex exec --json`` JSONL into (thread_id, text, usage).

        Only three event types matter: ``thread.started`` (session id),
        ``item.completed`` with ``item.type == 'agent_message'`` (reply
        text, concatenated), and ``turn.completed`` (usage). Unknown
        types — and unparsable lines — are skipped, which is exactly why
        this adapter needs no SDK-style protocol shim (#190).
        """
        thread_id: str | None = None
        texts: list[str] = []
        usage: dict[str, Any] | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            etype = obj.get("type")
            if etype == "thread.started":
                tid = obj.get("thread_id")
                if isinstance(tid, str) and tid:
                    thread_id = tid
            elif etype == "item.completed":
                item = obj.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    txt = item.get("text")
                    if isinstance(txt, str) and txt:
                        texts.append(txt)
            elif etype == "turn.completed":
                u = obj.get("usage")
                if isinstance(u, dict):
                    usage = u
        return thread_id, ("\n".join(texts) if texts else None), usage

    @staticmethod
    def _extract_usage(
        usage: dict[str, Any] | None, model: str | None
    ) -> dict[str, Any]:
        """Map ``turn.completed.usage`` to the EngineTurn usage shape.

        codex reports ``input_tokens``/``output_tokens`` (plus
        ``cached_input_tokens``/``reasoning_output_tokens`` we don't
        surface yet) and NO cost, so ``cost_usd`` stays None. Defensive:
        a missing/odd usage yields model-only with None tokens.
        """

        def _int(v: Any) -> int | None:
            if isinstance(v, bool):
                return None
            if isinstance(v, int):
                return v
            if isinstance(v, float):
                return int(v)
            return None

        if not isinstance(usage, dict):
            return {
                "model": model,
                "input_tokens": None,
                "output_tokens": None,
                "cost_usd": None,
            }
        return {
            "model": model,
            "input_tokens": _int(usage.get("input_tokens")),
            "output_tokens": _int(usage.get("output_tokens")),
            "cost_usd": None,
        }

    def _take_last_usage(self) -> dict[str, Any] | None:
        """Pop the usage parsed during the last ``_call_codex`` (#461)."""
        usage = self._last_usage
        self._last_usage = None
        return usage

    async def ingest_context(self, msg: dict[str, Any]) -> None:
        """Buffer an INGEST_ONLY message for the next active turn."""
        room_id = msg.get("room_id") or "_default"
        line = format_context_line(msg)
        if line is None:
            return
        append_context_line(self._pending_context, room_id, line)

    async def stop(self) -> None:
        self._room_thread_ids.clear()
        self._pending_context.clear()


async def integrate_with_codex_cli(
    client: ChatClient,
    model: str | None = None,
    system_prompt: str = "You are a helpful team member in a multi-agent chat. Answer concisely.",
    reasoning_effort: str | None = None,
    permission_level: str | None = None,
) -> CodexCliAdapter:
    """Hook incoming messages to the ``codex exec`` CLI.

    The host machine must have ``codex`` installed and authenticated.
    Returns the adapter for lifecycle management. ``permission_level``
    (#309) falls back to the spawner's ``ANYGARDEN_AGENT_PERMISSION_LEVEL``
    env var when None (cluster pushes the tier as env; the adapter
    resolves it to native CLI flags).
    """
    if permission_level is None:
        env_tier = os.environ.get("ANYGARDEN_AGENT_PERMISSION_LEVEL")
        if env_tier:
            permission_level = env_tier
    adapter = CodexCliAdapter(
        model=model,
        system_prompt=system_prompt,
        reasoning_effort=reasoning_effort,
        permission_level=permission_level,
    )
    adapter._client = client
    await adapter.start()

    engine_timeout = resolve_supervisor_timeout(_CODEX_CLI_TIMEOUT)
    supervisor = RoomHandlerSupervisor(
        client=client, engine_name="codex-cli", engine_timeout=engine_timeout
    )

    @client.on_message
    async def _handle(msg: dict[str, Any]) -> None:
        room_id = msg.get("room_id", "")

        from anygarden_agent.integrations.base import MessagePolicy, decide_policy

        policy = decide_policy(msg, client)
        if policy is MessagePolicy.SKIP:
            return
        if policy is MessagePolicy.INGEST_ONLY:
            await adapter.ingest_context(msg)
            return

        from anygarden_agent.integrations.delegate import (
            execute_delegate,
            parse_delegate,
        )

        delegate = parse_delegate(msg.get("content", ""))
        if delegate:
            await execute_delegate(client, msg, delegate)
            return

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
                adapter._take_turn_input(room_id)
                adapter._take_last_usage()

        await supervisor.dispatch(
            room_id=room_id,
            request_id=request_id,
            run_engine=run_engine,
        )

    return adapter
