"""Google Gemini CLI integration — subprocess-based adapter using ``gemini -p``.

The Gemini CLI (``gemini`` binary from https://github.com/google-gemini/gemini-cli)
uses its own ``findProjectRoot`` that walks upward looking for a
``.git`` directory. In our per-agent layout there is no ``.git`` in
``agent_root/workspace/`` (nor in ``agent_root/``), so gemini would
treat whichever cwd the adapter launches it from as the project root
and look for ``.gemini/settings.json`` there. The materializer drops
``.gemini/settings.json`` at ``agent_root/.gemini/settings.json`` and
``AGENTS.md`` at ``agent_root/AGENTS.md`` — so to make gemini actually
pick those up as hierarchical memory (auto-loaded system context, not
just a file the LLM happens to read) the adapter pins the subprocess
cwd to ``agent_root`` (i.e. ``Path.cwd().parent``). With that cwd:

- ``.gemini/settings.json`` is in cwd → settings (including
  ``context.fileName = "AGENTS.md"``) are actually loaded
- ``AGENTS.md`` is in cwd → hierarchical memory auto-loads it, so
  skill rules apply even without the user prompt referencing them
- ``workspace/`` is a subdirectory → the LLM can still write scratch
  files there via relative paths

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
from doorae_agent.runtime.handler_wrapper import RoomHandlerSupervisor

logger = structlog.get_logger(__name__)

# Gemini CLI call timeout. The codex adapter (Issue #190) uses 600s
# because its tool turns can reason for several minutes; gemini's
# shorter 120s fits its faster turn profile. Shorter timeouts than
# this bite under real tool use where retrieval + reasoning can take
# a minute.
_GEMINI_TIMEOUT = 120


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
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._reasoning_effort = reasoning_effort
        self._gemini_path: str | None = None
        # Per-room conversation history to prevent cross-room leaks.
        # Gemini CLI in headless mode is stateless per invocation, so
        # we rebuild the prompt from history each call (same approach
        # CodexAdapter uses).
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

    async def start(self) -> None:
        """Verify that the gemini CLI is installed and reachable."""
        self._gemini_path = shutil.which("gemini")
        if self._gemini_path:
            logger.info("gemini.found", path=self._gemini_path)
            # Debug breadcrumb: did the materializer drop AGENTS.md
            # and .gemini/settings.json at agent_root (one level
            # above the agent subprocess cwd)? We launch gemini with
            # cwd=agent_root so this is exactly where gemini will
            # look for hierarchical memory.
            try:
                agent_root = Path.cwd().parent
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

        # #74: drain any pending context lines into a prefix before
        # the user's own content. Gemini's stateless-per-invocation
        # model means the prefix flows into this turn's transcript
        # and also stays in the per-room ``_conversations`` history,
        # so subsequent turns keep the breadcrumb as prior context.
        prefix = drain_context(self._pending_context, room_id)
        turn_content = f"{prefix}\n\n{content}" if prefix else content

        # Build prompt with per-room conversation context.
        conversation.append({"role": "user", "content": turn_content})
        prompt = self._build_prompt(conversation, room_id=room_id)

        try:
            response = await self._call_gemini(prompt)
            if response:
                conversation.append({"role": "assistant", "content": response})
                return response
            # No response — roll back the user turn so a subsequent
            # call doesn't repeat context the model never saw.
            conversation.pop()
            return None
        except Exception as exc:
            logger.error("gemini.exec_failed", error=str(exc))
            conversation.pop()
            return None

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
        # Issue #237 — cross-engine memory suffix. Appended to the
        # system-prompt preamble (before the dialogue state) so the
        # agent reads it first.
        from doorae_agent.integrations.base import compose_memory_suffix

        memory_suffix = compose_memory_suffix(
            getattr(self, "_client", None), room_id
        )
        if memory_suffix:
            parts.append(memory_suffix)
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

        The subprocess cwd is pinned to ``agent_root`` (i.e. one level
        up from the agent Python process cwd, which is ``workspace/``).
        Gemini's ``findProjectRoot`` walks upward from cwd looking for
        ``.git``; in our layout there is none, so whatever we pass as
        cwd becomes its project root. We want that project root to be
        ``agent_root`` so gemini finds ``.gemini/settings.json`` and
        the ``AGENTS.md`` context file materialized there. If we left
        cwd at ``workspace/`` gemini would never see our settings or
        hierarchical memory — it would behave like a stock session.
        """
        agent_root = Path.cwd().parent
        cmd = [
            self._gemini_path,
            "-p", prompt,
            "--output-format", "json",
            # Auto-approve all tool calls. Non-interactive gemini
            # otherwise blocks on the default "prompt for approval"
            # mode the moment a skill asks it to run a shell command
            # or read a file, and there is no human behind the
            # subprocess to say "yes" — the call just hangs until
            # the timeout. The same trust model applies to our
            # codex and claude-code adapters (both run unattended).
            "--approval-mode", "yolo",
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        if self._reasoning_effort:
            # Gemini CLI uses --thinking-budget for reasoning effort.
            # Map doorae levels to gemini budget values.
            _budget_map = {"low": "1024", "medium": "8192", "high": "32768"}
            budget = _budget_map.get(self._reasoning_effort, self._reasoning_effort)
            cmd.extend(["--thinking-budget", budget])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(agent_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group for clean kill
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_GEMINI_TIMEOUT
            )
        except asyncio.TimeoutError:
            # Kill the entire process group (gemini + child bash/npm/node).
            # Without this, proc.kill() only hits the direct child and
            # grandchildren survive as orphans under PID 1.
            import os, signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            logger.error("gemini.timeout", timeout=_GEMINI_TIMEOUT)
            return None

        if proc.returncode != 0:
            logger.error(
                "gemini.nonzero_exit",
                code=proc.returncode,
                stderr=stderr.decode(errors="replace")[:500],
            )
            return None

        return self._parse_response(stdout.decode(errors="replace"))

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


async def integrate_with_gemini_cli(
    client: ChatClient,
    model: str | None = None,
    system_prompt: str = "You are a helpful team member in a multi-agent chat. Answer concisely.",
    reasoning_effort: str | None = None,
) -> GeminiCliAdapter:
    """Hook incoming messages to the Gemini CLI.

    The host machine must have ``gemini`` installed and authenticated.
    Returns the adapter instance for lifecycle management.
    """
    adapter = GeminiCliAdapter(model=model, system_prompt=system_prompt, reasoning_effort=reasoning_effort)
    # #237 — hook the client so ``_build_prompt`` can pull the memory /
    # ephemeral suffix from the welcome-frame cache.
    adapter._client = client
    await adapter.start()

    engine_timeout = float(
        os.environ.get("DOORAE_AGENT_ENGINE_TIMEOUT_SEC", "900")
    )
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

        # #204 — supervisor-routed path; see codex.py for rationale.
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
