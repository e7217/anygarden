"""Google Gemini CLI integration — subprocess-based adapter using ``gemini -p``.

The Gemini CLI (``gemini`` binary from https://github.com/google-gemini/gemini-cli)
uses its own ``findProjectRoot`` that walks upward looking for a
``.git`` directory. In our per-agent layout there is no ``.git`` in
``agent_root/``, so gemini treats whichever cwd the adapter launches it
from as the project root and looks for ``.gemini/settings.json`` there.
The materializer drops ``.gemini/settings.json`` at
``agent_root/.gemini/settings.json`` and ``AGENTS.md`` at
``agent_root/AGENTS.md``; the machine spawner now launches the agent
process from that same ``agent_root``. With that cwd:

- ``.gemini/settings.json`` is in cwd → settings (including
  ``context.fileName = "AGENTS.md"``) are actually loaded
- ``AGENTS.md`` is in cwd → hierarchical memory auto-loads it, so
  skill rules apply even without the user prompt referencing them
- runtime files can be written relative to the agent directory while
  managed config/instruction files are refreshed on each spawn

The adapter's job is kept narrow:

- detect that the ``gemini`` binary exists on the host
- forward each chat message as ``gemini -p "<prompt>" --output-format json``
- parse the JSON response
- keep per-room conversation context so two rooms on the same agent
  don't cross-contaminate

MCP servers live in ``.gemini/settings.json`` materialized by
``Spawner._materialize_agent_dir``. API keys flow into the agent
subprocess environment via ``Spawner.spawn`` (see #184) — they are
no longer rendered to a ``.gemini/.env`` file, because that file
was readable from the agent's tool sandbox and the LLM's ``Read``
tool could exfiltrate the plaintext key.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil
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
    EngineTurn,
    RoomHandlerSupervisor,
    is_transient_error,
)
from anygarden_agent.integrations._turn_timeout import (
    resolve_supervisor_timeout,
    resolve_turn_timeout,
)

logger = structlog.get_logger(__name__)


# Issue #309 — semantic permission tier → gemini-cli native flag
# mapping. Gemini's surface differs from codex: it has no OS-level
# sandbox, only ``--approval-mode`` (yolo / default) and the
# ``--skip-trust`` cwd trust opt-in. ``standard`` matches the
# pre-#309 behaviour (yolo + skip-trust); ``restricted`` drops both
# so gemini refuses tool calls instead of auto-approving and the
# agent cwd is not granted folder trust. ``trusted`` keeps the
# pre-#309 flags — gemini can already invoke shell tools freely
# under yolo, so there is no extra dial to relax.
def _resolve_gemini_flags(
    permission_level: str | None,
) -> dict[str, bool]:
    """Translate a permission tier into the gemini cli flag set.

    Returns a dict with ``approval_yolo`` and ``skip_trust`` booleans;
    the adapter's ``_call_gemini`` reads these to decide which CLI
    flags to append. Unknown tiers raise ``ValueError`` so a typo
    fails loud rather than silently falling back to ``standard``.
    """
    if permission_level is None or permission_level == "standard":
        return {"approval_yolo": True, "skip_trust": True}
    if permission_level == "restricted":
        return {"approval_yolo": False, "skip_trust": False}
    if permission_level == "trusted":
        # Same as standard for gemini — there's no host-access dial
        # beyond what yolo already grants. The tier label still
        # propagates so future gemini features (e.g. an explicit
        # ``--dangerously-allow-host-access`` flag) can plug in here.
        return {"approval_yolo": True, "skip_trust": True}
    raise ValueError(
        f"unknown permission_level: {permission_level!r} — "
        "expected one of ('restricted', 'standard', 'trusted')"
    )


def _subprocess_group_kwargs() -> dict[str, Any]:
    """Cross-platform Popen kwargs that put the child in its own group.

    POSIX: ``setsid`` so children can be terminated as a tree.
    Windows: ``CREATE_NEW_PROCESS_GROUP`` for the analogous isolation.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _terminate_tree(pid: int, timeout: float) -> None:
    """Terminate *pid* and all descendants. Tolerates already-dead PIDs."""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        victims = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        victims = [root]
    for proc in victims:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(victims, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass

# Gemini CLI call timeout. The codex adapter (Issue #190) uses 600s
# because its tool turns can reason for several minutes; gemini's
# shorter 120s fits its faster turn profile. Shorter timeouts than
# this bite under real tool use where retrieval + reasoning can take
# a minute.
# #492 — resolved via the shared helper so gemini gains an env override
# (``ANYGARDEN_AGENT_GEMINI_TURN_TIMEOUT_SEC``); the 120s default profile is
# preserved when unset.
_GEMINI_TIMEOUT = resolve_turn_timeout("gemini")


class GeminiCliAdapter(EngineAdapter):
    """Adapter that calls the host-installed ``gemini`` CLI via subprocess.

    The host machine must have ``gemini`` installed and authenticated
    (``GEMINI_API_KEY`` env var — the daemon injects it into the agent
    subprocess env from ``engine_secrets``, per #184 — or gcloud ADC).
    """

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str = "You are a helpful team member in a multi-agent chat. Answer concisely.",
        reasoning_effort: str | None = None,
        permission_level: str | None = None,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._reasoning_effort = reasoning_effort
        # Issue #309 — semantic permission tier. ``None`` is treated
        # as ``standard`` (= pre-#309 hardcoded yolo + skip-trust)
        # via ``_resolve_gemini_flags``. ``restricted`` swaps to
        # default approval mode (gemini will refuse tool calls
        # rather than auto-approve) and drops ``--skip-trust`` so
        # the agent cwd is not granted folder trust.
        self._permission_level = permission_level
        self._gemini_path: str | None = None
        # Per-room conversation history to prevent cross-room leaks.
        # Gemini CLI in headless mode is stateless per invocation, so
        # we rebuild the prompt from history each call (same approach
        # CodexCliAdapter uses).
        self._conversations: dict[str, list[dict[str, str]]] = {}
        # Per-room pending context buffer (#74 Stage B). Populated by
        # ``ingest_context`` and drained as a prompt prefix on the
        # next active turn — see ``coordination.pending_context`` for
        # the shared policy.
        self._pending_context: dict[str, list[tuple[float, str]]] = {}
        # #237 — owning client reference for cross-engine memory
        # suffix composition. ``integrate_with_gemini_cli`` wires this
        # after ``start()``; tests that instantiate the adapter directly
        # leave it None and the suffix helper degrades to empty string.
        self._client: ChatClient | None = None
        # #461 (Wave 2d) — last turn's LLM usage parsed from the gemini
        # CLI's ``--output-format json`` ``stats`` block (token counts +
        # resolved model name; gemini reports no cost, so ``cost_usd``
        # stays None). Stashed inside ``_call_gemini`` and read back by
        # the run_engine closure right after ``on_message`` returns so the
        # supervisor can surface it on the ``engine_call_finished`` frame.
        # ``None`` between turns / when no parsable stats were emitted.
        self._last_usage: dict[str, Any] | None = None

    async def start(self) -> None:
        """Verify that the gemini CLI is installed and reachable."""
        self._gemini_path = shutil.which("gemini")
        if self._gemini_path:
            logger.info("gemini.found", path=self._gemini_path)
            # Debug breadcrumb: did the materializer drop AGENTS.md
            # and .gemini/settings.json at the agent cwd? We launch
            # gemini from the same directory so this is exactly where
            # it will look for hierarchical memory.
            try:
                agent_root = Path.cwd()
                agents_md = agent_root / "AGENTS.md"
                settings = agent_root / ".gemini" / "settings.json"
                if agents_md.is_file():
                    logger.info("gemini.agents_md_found", path=str(agents_md))
                else:
                    logger.debug("gemini.no_agents_md", agent_root=str(agent_root))
                if settings.is_file():
                    logger.info("gemini.settings_found", path=str(settings))
                else:
                    logger.debug("gemini.no_settings", agent_root=str(agent_root))
            except Exception:
                pass
        else:
            logger.warning(
                "gemini.not_found",
                hint="Install gemini-cli: npm i -g @google/gemini-cli",
            )

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Forward the message to ``gemini -p`` and return the response."""
        if self._gemini_path is None:
            return None

        content = msg.get("content", "")
        if not content:
            return None

        room_id = msg.get("room_id", "_default")
        conversation = self._conversations.setdefault(room_id, [])

        # Issue #286 — drain + ``<room_conversation>`` wrap +
        # concat is the standard pipeline shared with claude_code
        # and codex; see ``EngineAdapter.assemble_user_content``.
        # Gemini's stateless-per-invocation CLI model means the
        # turn_content flows into both this transcript and the
        # per-room ``_conversations`` history, so subsequent turns
        # keep the wrapped breadcrumb as prior context.
        metadata = msg.get("metadata")
        turn_content = self.assemble_user_content(
            room_id,
            content,
            metadata if isinstance(metadata, dict) else None,
        )

        # Build prompt with per-room conversation context.
        conversation.append({"role": "user", "content": turn_content})
        prompt = self._build_prompt(conversation, room_id=room_id)
        # #433 — gemini flattens system+memory+roster+transcript into this
        # single -p argument, so it is the fullest turn input of the four
        # adapters. Stash it for the run_engine closure to surface.
        self._record_turn_input(room_id, prompt)

        try:
            response = await self._call_gemini(prompt)
            if response:
                conversation.append({"role": "assistant", "content": response})
                return response
            # No response — roll back the user turn so a subsequent
            # call doesn't repeat context the model never saw.
            conversation.pop()
            return None
        except EngineError:
            conversation.pop()
            raise
        except Exception as exc:
            logger.error("gemini.exec_failed", error=str(exc))
            conversation.pop()
            # #422 — propagate so the supervisor records outcome=failed
            # and notifies the user instead of returning None (silent).
            # #457 — classify conn-reset / upstream-5xx as transient.
            raise EngineError(
                str(exc), transient=is_transient_error(str(exc))
            ) from exc

    async def ingest_context(self, msg: dict[str, Any]) -> None:
        """Buffer an ``INGEST_ONLY`` message for the next active turn.

        Gemini owns no persistent session between CLI invocations,
        so the breadcrumb only survives as long as this adapter
        instance. Stashed in ``_pending_context``; rendered into
        the next ``on_message`` prompt prefix.
        """
        room_id = msg.get("room_id") or "_default"
        line = format_context_line(msg)
        if line is None:
            return
        append_context_line(self._pending_context, room_id, line)

    async def stop(self) -> None:
        self._conversations.clear()
        self._pending_context.clear()

    def _build_prompt(
        self, conversation: list[dict[str, str]], room_id: str | None = None
    ) -> str:
        """Build a single prompt string from per-room conversation history.

        The Gemini CLI non-interactive (``-p``) mode treats the prompt
        as a single user turn, so we flatten the conversation into a
        tagged transcript. AGENTS.md handles the system-prompt layer
        at the CLI level via ``context.fileName``; this method only
        carries the dialogue state.

        #237 — when ``room_id`` is supplied the memory / ephemeral
        block is appended to the system-prompt preamble. Kept optional
        so the legacy call sites (tests) don't break.
        """
        parts: list[str] = []
        if self._system_prompt:
            parts.append(self._system_prompt)
            parts.append("")
        # Issue #237 / #279 / #293 — cross-engine memory + roster
        # suffix. Appended to the system-prompt preamble (before the
        # dialogue state) so the agent reads it first. Gemini CLI
        # non-interactive mode is stateless (each ``-p`` invocation
        # spawns a fresh process) so re-injecting every turn is
        # cheap and consistent — no sha tracking needed. Solo agents
        # see the pre-#279 prompt byte-for-byte (helper returns "").
        from anygarden_agent.integrations.base import (
            compose_session_context_suffix,
        )

        client = getattr(self, "_client", None)
        is_collab = (
            client is not None
            and room_id is not None
            and client.is_collaborative(room_id)
        )
        suffix = compose_session_context_suffix(
            client,
            room_id,
            include_roster=is_collab,
            with_collaborative_hint=is_collab,
        )
        if suffix:
            parts.append(suffix)
            parts.append("")
        for turn in conversation:
            role = turn["role"]
            text = turn["content"]
            if role == "user":
                parts.append(f"[User] {text}")
            else:
                parts.append(f"[You] {text}")
        parts.append("")
        parts.append("Respond concisely as a team member.")
        return "\n".join(parts)

    async def _call_gemini(self, prompt: str) -> str | None:
        """Invoke ``gemini -p <prompt> --output-format json``.

        The subprocess cwd is pinned to ``agent_root``, which is also
        the agent Python process cwd. Gemini's ``findProjectRoot`` walks
        upward from cwd looking for
        ``.git``; in our layout there is none, so whatever we pass as
        cwd becomes its project root. We want that project root to be
        ``agent_root`` so gemini finds ``.gemini/settings.json`` and
        the ``AGENTS.md`` context file materialized there.
        """
        agent_root = Path.cwd()
        # Issue #309 — derive the approval/trust flags from the
        # permission tier. ``restricted`` agents skip yolo (gemini
        # refuses tool calls non-interactively) and skip the trust
        # opt-in. ``standard`` (incl. ``None`` / ``trusted``)
        # preserves the pre-#309 hardcoded combination.
        flags = _resolve_gemini_flags(self._permission_level)
        cmd = [
            self._gemini_path,
            "-p", prompt,
            "--output-format", "json",
        ]
        if flags["approval_yolo"]:
            # Auto-approve all tool calls. Non-interactive gemini
            # otherwise blocks on the default "prompt for approval"
            # mode the moment a skill asks it to run a shell command
            # or read a file, and there is no human behind the
            # subprocess to say "yes". Same trust model as the
            # codex / claude-code adapters under ``standard``.
            cmd.extend(["--approval-mode", "yolo"])
        if flags["skip_trust"]:
            # #261 — gemini 0.39.x silently downgrades yolo to
            # "default" when cwd is not in trustedFolders.json,
            # then exits 55 in non-interactive mode. agent_root is
            # a fresh UUID dir per spawn so it can't be pre-trusted;
            # this flag trusts the agent cwd for this session only.
            # ``restricted`` deliberately drops it so the cwd
            # stays untrusted and gemini's stricter posture kicks in.
            cmd.append("--skip-trust")
        if self._model:
            cmd.extend(["--model", self._model])
        if self._reasoning_effort:
            # Gemini CLI uses --thinking-budget for reasoning effort.
            # Map anygarden levels to gemini budget values.
            _budget_map = {"low": "1024", "medium": "8192", "high": "32768"}
            budget = _budget_map.get(self._reasoning_effort, self._reasoning_effort)
            cmd.extend(["--thinking-budget", budget])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(agent_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_subprocess_group_kwargs(),  # own process group for clean kill
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_GEMINI_TIMEOUT
            )
        except asyncio.TimeoutError as exc:
            # Kill the entire process tree (gemini + child bash/npm/node).
            # Without this, proc.kill() only hits the direct child and
            # grandchildren survive as orphans.
            await asyncio.to_thread(_terminate_tree, proc.pid, 5.0)
            await proc.wait()
            logger.error("gemini.timeout", timeout=_GEMINI_TIMEOUT)
            # #422 — surface as timeout so the supervisor notifies the user.
            raise EngineTimeoutError(
                f"gemini turn exceeded {_GEMINI_TIMEOUT}s"
            ) from exc

        if proc.returncode != 0:
            stderr_snippet = stderr.decode(errors="replace")[:500]
            logger.error(
                "gemini.nonzero_exit",
                code=proc.returncode,
                stderr=stderr_snippet,
            )
            # #422 — a non-zero exit was previously swallowed as None,
            # producing a silent lost turn. Raise so the supervisor maps
            # it to outcome=failed and sends the failure notice regardless
            # of request_id (on_message re-raises EngineError cleanly).
            # #457 — a 429/5xx in the stderr snippet is a clearly-transient
            # upstream failure; tag it so the opt-in retry (default OFF)
            # may re-run the empty turn.
            raise EngineError(
                f"gemini exited with code {proc.returncode}"
                + (f": {stderr_snippet}" if stderr_snippet.strip() else ""),
                transient=is_transient_error(stderr_snippet),
            )

        raw = stdout.decode(errors="replace")
        # #461 (Wave 2d) — parse the ``stats`` block for token usage before
        # extracting the reply text. Stashed on the instance so the
        # run_engine closure can surface it on the engine_call_finished
        # frame; defensive (a miss leaves it None, never raises).
        self._last_usage = self._extract_gemini_usage(raw, self._model)
        return self._parse_response(raw)

    @staticmethod
    def _parse_response(raw: str) -> str | None:
        """Extract the response text from ``gemini --output-format json``.

        The JSON schema gemini emits varies across versions but the
        response is conventionally under one of: ``response``,
        ``text``, or ``content``. Try each in order and fall back to
        the raw string if nothing matches — at worst the user sees
        the JSON, which is better than swallowing the output.
        """
        raw = raw.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("gemini.invalid_json", preview=raw[:200])
            return raw

        if isinstance(data, dict):
            for key in ("response", "text", "content", "output"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            # Nothing matched; dump the whole blob so at least
            # something lands in the room.
            return json.dumps(data, ensure_ascii=False)
        if isinstance(data, str):
            return data
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _extract_gemini_usage(
        raw: str, fallback_model: str | None
    ) -> dict[str, Any] | None:
        """Parse token usage from a ``gemini --output-format json`` payload.

        #461 (Wave 2d) — the gemini CLI's JSON output carries a ``stats``
        block: ``stats.models`` maps each resolved model name to a
        ``{api, tokens}`` record whose ``tokens`` object exposes
        ``prompt`` (input) and ``candidates`` (output) counts (verified
        against gemini-cli 0.39's bundled telemetry schema). We sum those
        across models and take the (first) model name as the label. The
        gemini CLI reports NO cost, so ``cost_usd`` is always None.

        Entirely best-effort: a missing / non-JSON / schema-drifted
        payload yields ``None`` (or model-only with None tokens), never
        raising — a turn whose stats couldn't be read still records the
        model + latency rather than dropping the row. ``fallback_model``
        (the adapter's configured model, possibly None) is used only when
        the stats block names no model.
        """
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, AttributeError):
            return None
        if not isinstance(data, dict):
            return None
        stats = data.get("stats")
        models = stats.get("models") if isinstance(stats, dict) else None
        if not isinstance(models, dict) or not models:
            return None

        model_name: str | None = None
        input_tokens = 0
        output_tokens = 0
        saw_tokens = False
        for name, rec in models.items():
            if model_name is None and isinstance(name, str) and name:
                model_name = name
            tokens = rec.get("tokens") if isinstance(rec, dict) else None
            if not isinstance(tokens, dict):
                continue
            # ``prompt`` == input tokens, ``candidates`` == output tokens.
            pt = tokens.get("prompt")
            ct = tokens.get("candidates")
            if isinstance(pt, (int, float)) and not isinstance(pt, bool):
                input_tokens += int(pt)
                saw_tokens = True
            if isinstance(ct, (int, float)) and not isinstance(ct, bool):
                output_tokens += int(ct)
                saw_tokens = True

        return {
            "model": model_name or fallback_model,
            "input_tokens": input_tokens if saw_tokens else None,
            "output_tokens": output_tokens if saw_tokens else None,
            "cost_usd": None,
        }

    def _take_last_usage(self) -> dict[str, Any] | None:
        """Pop the usage record parsed during the last ``_call_gemini``.

        #461 — read by the run_engine closure right after ``on_message``
        returns so the parsed counts are surfaced on the EngineTurn
        exactly once and never leak into a later turn.
        """
        usage = self._last_usage
        self._last_usage = None
        return usage


async def integrate_with_gemini_cli(
    client: ChatClient,
    model: str | None = None,
    system_prompt: str = "You are a helpful team member in a multi-agent chat. Answer concisely.",
    reasoning_effort: str | None = None,
    permission_level: str | None = None,
) -> GeminiCliAdapter:
    """Hook incoming messages to the Gemini CLI.

    The host machine must have ``gemini`` installed and authenticated.
    Returns the adapter instance for lifecycle management.

    ``permission_level`` (#309) — when None, the spawner's
    ``ANYGARDEN_AGENT_PERMISSION_LEVEL`` env var is consulted so the
    CLI entry can stay tier-agnostic (cluster pushes the value as
    env, the adapter resolves to native flags).
    """
    if permission_level is None:
        env_tier = os.environ.get("ANYGARDEN_AGENT_PERMISSION_LEVEL")
        if env_tier:
            permission_level = env_tier
    adapter = GeminiCliAdapter(
        model=model,
        system_prompt=system_prompt,
        reasoning_effort=reasoning_effort,
        permission_level=permission_level,
    )
    # #237 — hook the client so ``_build_prompt`` can pull the memory /
    # ephemeral suffix from the welcome-frame cache.
    adapter._client = client
    await adapter.start()

    engine_timeout = resolve_supervisor_timeout(_GEMINI_TIMEOUT)
    supervisor = RoomHandlerSupervisor(
        client=client, engine_name="gemini", engine_timeout=engine_timeout
    )

    @client.on_message
    async def _handle(msg: dict[str, Any]) -> None:
        room_id = msg.get("room_id", "")

        # 3-state gate (#74, #148). SKIP drops the message;
        # INGEST_ONLY stashes it for the next active turn's prompt
        # prefix; RESPOND proceeds below. The server decides ambient
        # candidacy via ``metadata.ingest_only`` (#148 Part 3); the
        # adapter just reacts to ``decide_policy``'s output.
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
                # #433 — pair the reply with the stashed turn input.
                # #461 — also surface the token usage parsed from the CLI's
                # JSON ``stats`` (model + input/output tokens; gemini
                # reports no cost).
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
                # Await the cancelled task before sending the False frame so
                # an in-flight sendTyping(True) cannot land after the False
                # (which would leave the indicator stuck on) and the
                # CancelledError is retrieved. Mirrors codex.py.
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
                await client.sendTyping(room_id, False)
                # #433 — drain the stash even when on_message raised, so a
                # failed turn never leaks/leaves a stale prompt. No-op on ok.
                adapter._take_turn_input(room_id)
                # #461 — likewise drain any usage stash a raised turn left.
                adapter._take_last_usage()

        await supervisor.dispatch(
            room_id=room_id,
            request_id=request_id,
            run_engine=run_engine,
        )

    return adapter
