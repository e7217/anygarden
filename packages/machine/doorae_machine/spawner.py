"""Agent subprocess spawn/kill/watch manager."""

from __future__ import annotations

import asyncio
import errno
import os
import shutil
import signal
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

import structlog

from doorae_machine.agent_dir import (
    validate_agent_file_path,
    validate_agent_id,
)
from doorae_machine.supervisor import watch_process

log = structlog.get_logger()

KILL_TIMEOUT = 10  # seconds to wait after SIGTERM before SIGKILL


@dataclass
class SpawnManifest:
    """Engine-agnostic spawn parameters.

    Decouples the spawner from protocol frames so the same spawn()
    method can be driven by SyncDesiredStateFrame + TokenGrantFrame
    or any future source.
    """

    agent_id: str
    engine: str
    agent_token: str
    profile_yaml: str = ""
    rooms: list[str] = field(default_factory=list)
    server_url: str = ""
    name: str = ""
    agents_md: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    engine_secrets: dict[str, str] = field(default_factory=dict)
    reasoning_effort: str | None = None
    sub_rooms: list[dict] = field(default_factory=list)


@dataclass
class RunningAgent:
    """Tracks a running agent subprocess."""

    agent_id: str
    pid: int
    engine: str
    started_at: float
    proc: asyncio.subprocess.Process
    watch_task: asyncio.Task | None = None
    profile_path: Path | None = None


@dataclass
class SpawnResult:
    """Result of a spawn attempt."""

    success: bool
    agent_id: str
    pid: int = 0
    error: str = ""


class Spawner:
    """Manages agent subprocess lifecycle: spawn, kill, watch."""

    def __init__(
        self,
        on_stopped: Callable[[str, int], Coroutine] | None = None,
        on_crashed: Callable[[str, int, str], Coroutine] | None = None,
        agent_server_url: str = "",
        agent_dirs_root: Path | None = None,
    ) -> None:
        self._agents: dict[str, RunningAgent] = {}
        self._on_stopped = on_stopped or self._default_on_stopped
        self._on_crashed = on_crashed or self._default_on_crashed
        # Per-agent directory root. Tests override this to tmp_path so
        # they don't pollute the real ~/.doorae/agents/.
        self._agent_dirs_root = (
            agent_dirs_root
            if agent_dirs_root is not None
            else Path.home() / ".doorae" / "agents"
        )
        # Base URL the daemon hands to agent subprocesses (e.g. ws://host:port).
        # When empty, falls back to SpawnManifest.server_url for backwards
        # compatibility with older daemons/servers.
        self._agent_server_url = agent_server_url

    async def _default_on_stopped(self, agent_id: str, exit_code: int) -> None:
        log.info("agent_stopped_default", agent_id=agent_id, exit_code=exit_code)

    async def _default_on_crashed(
        self, agent_id: str, exit_code: int, stderr_tail: str
    ) -> None:
        log.warning(
            "agent_crashed_default",
            agent_id=agent_id,
            exit_code=exit_code,
            stderr_tail=stderr_tail[:200],
        )

    # ── Per-agent directory materialization ──────────────────────────────

    # Mapping from engine name to the .env file we render engine_secrets
    # into. Each engine's CLI auto-loads its own dotenv from cwd.
    _ENGINE_ENV_PATHS: dict[str, str] = {
        "gemini-cli": ".gemini/.env",
        "codex": ".codex/.env",
        "claude-code": ".claude/.env",
    }

    @staticmethod
    def _compose_agents_md(msg: SpawnManifest) -> str:
        """Return the AGENTS.md body rendered from the manifest.

        Base content is ``msg.agents_md`` verbatim. If the manifest
        contains any ``skills/<name>/SKILL.md`` files, their bodies
        are auto-inlined into a trailing ``## Available skills``
        section sorted by path for deterministic output.

        Why: codex CLI (and other engines that only read AGENTS.md)
        does not natively discover project-local skills the way
        Claude Code's ``.claude/skills/`` does. Without this
        auto-inline, an admin would have to manually paste skill
        bodies into AGENTS.md to make codex aware of them, which
        both duplicates content and drifts out of sync with the
        actual ``skills/*/SKILL.md`` files on disk. Auto-inline
        keeps AGENTS.md the single projection point for engines
        that cannot load skills themselves, while the raw
        ``skills/*/SKILL.md`` files are still written to disk for
        engines (Claude Code, gemini-cli) that DO discover them
        natively.
        """
        base = msg.agents_md or ""
        sections: list[str] = [base.rstrip()]

        # ── Skills auto-inline ──────────────────────────────────
        skill_paths = sorted(
            path for path in msg.files
            if path.startswith("skills/") and path.endswith("/SKILL.md")
        )
        if skill_paths:
            sections.append("")
            sections.append("## Available skills")
            sections.append("")
            sections.append(
                "(auto-generated from the on-disk skills/ directory; "
                "engines that do not natively discover project skills "
                "read them from this section)"
            )
            for path in skill_paths:
                body = msg.files[path]
                sections.append("")
                sections.append(f"### `{path}`")
                sections.append("")
                sections.append(body.strip())

        # ── Delegation auto-inline ──────────────────────────────
        if msg.sub_rooms:
            sections.append("")
            sections.append("## Delegation")
            sections.append("")
            sections.append(
                "Sub-rooms you can delegate to using /delegate command. "
                "When a task matches a sub-room's purpose, delegate "
                "instead of answering directly. Report the result back "
                "to the current room.\n\n"
                "IMPORTANT: When you receive a [DELEGATED] task in a "
                "sub-room, answer the question concisely and STOP. "
                "Do NOT ask follow-up questions, do NOT suggest next "
                "steps, do NOT continue the conversation. Just provide "
                "the answer and finish."
            )
            sections.append("")
            for sr in msg.sub_rooms:
                name = sr.get("name", "")
                desc = sr.get("description") or ""
                if desc:
                    sections.append(f"- **{name}**: {desc}")
                else:
                    sections.append(f"- **{name}**")
                sections.append(f"  → /delegate {name} <task>")

        # ── Memory auto-inline ──────────────────────────────────
        sections.append("")
        sections.append("## Memory")
        sections.append("")
        sections.append(
            "You have a persistent memory file at `workspace/MEMORY.md`. "
            "At the START of every session, read this file to restore "
            "context from prior conversations.\n\n"
            "At the END of a conversation (before going idle), write a "
            "concise summary to `workspace/MEMORY.md` capturing:\n"
            "- Your current role and active context\n"
            "- Key decisions made and their rationale\n"
            "- Ongoing tasks and their status\n"
            "- Important facts about the workspace or project\n\n"
            "Keep the file short (under 500 words). Overwrite the entire "
            "file each time — do not append. The file persists across "
            "sessions on the same machine."
        )

        # Only add trailing newline if we appended extra sections.
        if len(sections) > 1:
            sections.append("")
        return "\n".join(sections)

    def _materialize_agent_dir(self, msg: SpawnManifest) -> Path:
        """Reconcile the on-disk agent directory with the spawn manifest.

        Builds ``<agent_dirs_root>/<agent_id>/`` so that after this call:

        - ``AGENTS.md`` mirrors ``msg.agents_md`` (absent if ``None``)
        - every entry in ``msg.files`` exists at that relative path
          with mode 0o600
        - engine-convention symlinks (``CLAUDE.md`` → ``AGENTS.md``,
          ``.agents/skills``/``.claude/skills`` → ``../skills``) are
          fresh
        - ``msg.engine_secrets`` is rendered to the engine-specific
          ``.env`` file if a mapping exists for ``msg.engine``
        - ``workspace/`` is preserved (the agent's runtime scratch)
        - **anything else** under the agent root is deleted, so files
          that dropped out of the manifest disappear from disk and
          the engine's cwd traversal no longer sees them

        Raises ``AgentFilePathError`` (from ``agent_dir.py``) if any
        manifest path fails validation — the spawn should then bail
        out before touching the filesystem.
        """
        # CRITICAL: validate agent_id BEFORE using it as a path segment.
        # ``Path(root) / agent_id`` does not protect against absolute
        # paths or ``..`` traversal, so a malicious spawn frame could
        # otherwise escape the managed root (e.g. ``agent_id="/etc"``
        # or ``agent_id="../other-agent"``) and the prune step would
        # then happily delete files outside the agent dir.
        validate_agent_id(msg.agent_id)

        # Pre-validate every file path before we touch the filesystem.
        # This way a bad manifest causes a clean failure rather than a
        # half-materialized directory.
        for path in msg.files.keys():
            validate_agent_file_path(path)

        agent_root = self._agent_dirs_root / msg.agent_id

        # Defense-in-depth: after joining, resolve the path and confirm
        # it is still under the root. This catches any residual quirks
        # the regex might miss (e.g. filesystem case-folding on macOS)
        # and documents the invariant at the call site.
        root_resolved = self._agent_dirs_root.resolve(strict=False)
        agent_resolved = agent_root.resolve(strict=False)
        if root_resolved != agent_resolved and root_resolved not in agent_resolved.parents:
            raise AgentFilePathError(
                f"agent_id {msg.agent_id!r} resolves outside the agent dir root"
            )

        agent_root.mkdir(parents=True, exist_ok=True)
        os.chmod(agent_root, 0o700)

        # --- Prune: wipe everything except workspace/ ------------------
        #
        # Walk the top-level entries of agent_root. For each entry:
        #   - skip ``workspace`` (runtime scratch lives here and must
        #     survive re-spawn)
        #   - symlinks: unlink (don't follow — otherwise we'd recurse
        #     into whatever the link points at)
        #   - files: unlink
        #   - directories: rmtree
        for entry in agent_root.iterdir():
            if entry.name == "workspace":
                continue
            try:
                if entry.is_symlink() or entry.is_file():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
            except OSError as exc:
                raise RuntimeError(
                    f"Failed to prune {entry} during materialize: {exc}"
                ) from exc

        # --- Write AGENTS.md from manifest -----------------------------
        #
        # Content is composed by _compose_agents_md: base agents_md
        # body plus an auto-inlined "## Available skills" section
        # carrying every skills/*/SKILL.md body. See the helper's
        # docstring for why codex needs this and why claude-code /
        # gemini-cli tolerate the extra content.
        if msg.agents_md is not None:
            agents_md = agent_root / "AGENTS.md"
            agents_md.write_text(self._compose_agents_md(msg))
            os.chmod(agents_md, 0o600)

        # --- Write each file in the manifest ---------------------------
        for rel_path, content in msg.files.items():
            target = agent_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(target.parent, 0o700)
            target.write_text(content)
            os.chmod(target, 0o600)

        # --- Synthetic symlinks (engine convention aliases) -----------
        # CLAUDE.md → AGENTS.md (Claude Code auto-discovers CLAUDE.md)
        # Only link when AGENTS.md actually exists; otherwise a dead
        # symlink would confuse the engine.
        if msg.agents_md is not None:
            claude_md = agent_root / "CLAUDE.md"
            if claude_md.exists() or claude_md.is_symlink():
                claude_md.unlink()
            claude_md.symlink_to("AGENTS.md")

        # .agents/skills and .claude/skills → ../skills (so Gemini CLI
        # and Claude Code both find the canonical skill directory).
        # Only create these if the manifest actually declared any
        # skills — otherwise the engines would follow dead links.
        if any(p.startswith("skills/") for p in msg.files):
            for alias_dir, target_rel in (
                (agent_root / ".agents" / "skills", "../skills"),
                (agent_root / ".claude" / "skills", "../skills"),
            ):
                alias_dir.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(alias_dir.parent, 0o700)
                if alias_dir.exists() or alias_dir.is_symlink():
                    # iterdir pruning above may not have removed these
                    # if they already existed as dirs; unlink now.
                    if alias_dir.is_symlink():
                        alias_dir.unlink()
                    else:
                        shutil.rmtree(alias_dir)
                alias_dir.symlink_to(target_rel)

        # --- Engine-specific .env -------------------------------------
        if msg.engine_secrets:
            env_rel = self._ENGINE_ENV_PATHS.get(msg.engine)
            if env_rel is not None:
                env_path = agent_root / env_rel
                env_path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(env_path.parent, 0o700)
                body = "".join(
                    f"{k}={v}\n" for k, v in sorted(msg.engine_secrets.items())
                )
                env_path.write_text(body)
                os.chmod(env_path, 0o600)

        # --- Ensure workspace/ exists ---------------------------------
        workspace = agent_root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        # Only chmod if we just created it; don't clobber permissions
        # the agent may have set on its own runtime files.
        try:
            os.chmod(workspace, 0o700)
        except PermissionError:
            pass

        # --- Seed workspace/MEMORY.md if absent -------------------------
        memory_md = workspace / "MEMORY.md"
        if not memory_md.exists():
            memory_md.write_text(
                "# Memory\n\n"
                "No prior context. This is the first session.\n"
            )
            os.chmod(memory_md, 0o600)

        # --- Narrow exception: bridge files inside workspace/ --------
        #
        # workspace/ is the agent's runtime scratch and is normally
        # excluded from the prune walk. We make a targeted exception
        # for a small set of MATERIALIZER-OWNED bridges that let the
        # engine discover canonical instructions without widening its
        # sandbox to agent_root:
        #
        #     workspace/AGENTS.md
        #     workspace/CLAUDE.md
        #
        # Codex CLI anchors AGENTS.md discovery at its ``-C <root>``
        # working root and does NOT walk upward. Claude Agent SDK
        # does the same for CLAUDE.md under its ``cwd`` option.
        # Gemini CLI walks upward to find ``.git`` and treats the
        # first ancestor (or cwd) as project root — same effect.
        # Without a copy or symlink inside cwd, adapters would have
        # to widen their "working root" flag to agent_root, which
        # widens the workspace-write sandbox enough to let the agent
        # rewrite its own instructions/config mid-session. Codex
        # stop-hook caught that failure mode once already.
        #
        # The shape of the bridge is **engine-specific** because the
        # engines disagree about symlinks:
        #
        # - Codex + Claude Code tolerate ``workspace/AGENTS.md ->
        #   ../AGENTS.md`` symlinks. Their sandboxes resolve the
        #   symlink on *read* so the engine sees the canonical
        #   content, and resolve it again on *write* — and because
        #   the resolved path (``agent_root/AGENTS.md``) is outside
        #   the ``workspace-write`` sandbox, write attempts via the
        #   agent's shell tool are rejected at the sandbox boundary.
        #   This is the isolation contract the Codex review signed
        #   off on: the canonical AGENTS.md is write-unreachable
        #   from inside the agent's sandbox.
        #
        # - Gemini CLI's file-reader tool rejects symlinks whose
        #   resolved path falls outside the allowed workspace
        #   directories. ``workspace/AGENTS.md -> ../AGENTS.md``
        #   resolves to ``agent_root/AGENTS.md`` which is outside
        #   workspace, so gemini refuses to even read it.
        #   ``Path not in workspace: Attempted path resolves outside
        #   the allowed workspace directories``.
        #
        # Resolution: default to the symlink form (keeps codex /
        # claude-code tight), and only write a real-file copy when
        # the engine is ``gemini-cli``. Real-file copies are marked
        # read-only (mode 0o400) as a speedbump against trivial
        # in-session tamper: a write via ``open(..., O_WRONLY)``
        # fails with EACCES because the owner has no write bit. The
        # agent can still chmod the file before writing (chmod is
        # not blocked by the sandbox), but the detour is loud enough
        # to show up in shell logs and the next spawn's materializer
        # overwrites the bytes regardless — tamper is still scoped
        # to a single session.
        #
        # Reconcile BOTH directions on every spawn:
        #
        # - ``agents_md`` set   → write fresh workspace/AGENTS.md +
        #   workspace/CLAUDE.md in whichever shape this engine
        #   prefers.
        # - ``agents_md`` None  → ensure both slots are absent;
        #   leaving a stale copy/symlink would expose the previous
        #   session's instructions to the next spawn even though
        #   the canonical tree was pruned.
        composed = self._compose_agents_md(msg) if msg.agents_md is not None else None
        use_real_copy = msg.engine == "gemini-cli"

        for slot_name in ("AGENTS.md", "CLAUDE.md"):
            slot = workspace / slot_name
            if slot.is_symlink() or slot.exists():
                slot.unlink()
            if composed is None:
                continue
            if use_real_copy:
                # Real file, read-only for the owner. The materializer
                # owns the bytes; the agent's session gets a snapshot,
                # not a mutable handle.
                slot.write_text(composed)
                os.chmod(slot, 0o400)
            else:
                # Symlink one level up. Reads resolve to the canonical
                # file; writes resolve to a path outside the sandbox
                # and the engine rejects them. This is the classic
                # "read-only view via symlink-plus-sandbox" pattern.
                slot.symlink_to(f"../{slot_name}")

        return agent_root

    async def spawn(self, msg: SpawnManifest) -> SpawnResult:
        """Spawn an agent subprocess.

        - Saves profile_yaml to a temp file (chmod 600)
        - Passes agent_token via DOORAE_TOKEN env var only (never argv)
        - Starts process via uvx
        - Begins background watch task
        """
        agent_id = msg.agent_id

        if agent_id in self._agents:
            old_pid = self._agents[agent_id].pid
            log.warning(
                "spawn.replacing_existing",
                agent_id=agent_id,
                old_pid=old_pid,
            )
            await self.kill(agent_id)

        # Materialize the per-agent directory from the spawn manifest.
        # Any entry in ``files`` that fails validation raises, which we
        # surface as a spawn failure.
        agent_root: Path | None = None
        try:
            agent_root = self._materialize_agent_dir(msg)
        except Exception as exc:
            return SpawnResult(
                success=False,
                agent_id=agent_id,
                error=f"Failed to materialize agent dir: {exc}",
            )

        # Write profile YAML to temp file with restricted permissions.
        # Use mkstemp for unpredictable filenames (avoids symlink attacks).
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=f"doorae-agent-{agent_id}-", suffix=".yaml"
            )
            profile_path = Path(tmp_path)
            with os.fdopen(fd, "w") as f:
                f.write(msg.profile_yaml)
            os.chmod(profile_path, 0o600)
        except OSError as exc:
            return SpawnResult(
                success=False,
                agent_id=agent_id,
                error=f"Failed to write profile: {exc}",
            )

        # Build environment: inherit current env + set DOORAE_TOKEN
        env = os.environ.copy()
        env["DOORAE_TOKEN"] = msg.agent_token

        # The daemon's own server URL is authoritative — it's the address the
        # daemon is connected to right now, so it's guaranteed reachable from
        # this host. Fall back to the frame-supplied URL only if the daemon
        # didn't provide one (older versions).
        agent_server = self._agent_server_url or msg.server_url

        # Build command — prefer local doorae-agent, fall back to uvx
        doorae_agent = shutil.which("doorae-agent")
        if doorae_agent:
            cmd = [
                doorae_agent,
                "--engine", msg.engine,
                "--name", msg.name or f"agent-{agent_id[:8]}",
                "--server", agent_server,
            ]
        else:
            # doorae-agent not in PATH — use uvx to fetch from PyPI
            cmd = [
                "uvx",
                "doorae-agent",
                "--engine", msg.engine,
                "--name", msg.name or f"agent-{agent_id[:8]}",
                "--server", agent_server,
            ]
        if msg.profile_yaml.strip():
            cmd.extend(["--profile", str(profile_path)])
        for room in msg.rooms:
            cmd.extend(["--room", room])
        if msg.reasoning_effort:
            cmd.extend(["--reasoning-effort", msg.reasoning_effort])

        # Spawn the subprocess with its cwd set to the agent's
        # workspace/. Engines that do upward file discovery (Codex
        # AGENTS.md scan, Claude Code CLAUDE.md scan, Gemini CLI
        # context scan) will then find the materialized files one level
        # up without any per-engine flag.
        workspace_cwd = str(agent_root / "workspace") if agent_root else None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                cwd=workspace_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            profile_path.unlink(missing_ok=True)
            return SpawnResult(
                success=False,
                agent_id=agent_id,
                error=f"Failed to start process: {exc}",
            )

        agent = RunningAgent(
            agent_id=agent_id,
            pid=proc.pid,
            engine=msg.engine,
            started_at=time.time(),
            proc=proc,
            profile_path=profile_path,
        )
        self._agents[agent_id] = agent

        # Start background watcher
        agent.watch_task = asyncio.create_task(
            watch_process(
                agent_id, proc, self._handle_stopped, self._handle_crashed
            )
        )

        log.info("agent_spawned", agent_id=agent_id, pid=proc.pid, engine=msg.engine)
        return SpawnResult(success=True, agent_id=agent_id, pid=proc.pid)

    async def _handle_stopped(self, agent_id: str, exit_code: int) -> None:
        """Handle normal agent stop, then delegate to callback."""
        await self._on_stopped(agent_id, exit_code)
        self._cleanup(agent_id)

    async def _handle_crashed(
        self, agent_id: str, exit_code: int, stderr_tail: str
    ) -> None:
        """Handle agent crash, then delegate to callback."""
        await self._on_crashed(agent_id, exit_code, stderr_tail)
        self._cleanup(agent_id)

    async def kill(self, agent_id: str) -> dict[str, Any]:
        """Kill a running agent: SIGTERM -> 10s wait -> SIGKILL."""
        agent = self._agents.get(agent_id)
        if agent is None:
            return {"success": False, "error": f"Agent {agent_id} not found"}

        proc = agent.proc
        try:
            proc.send_signal(signal.SIGTERM)
            log.info("agent_sigterm", agent_id=agent_id, pid=agent.pid)
        except ProcessLookupError:
            self._cleanup(agent_id)
            return {"success": True, "note": "Process already exited"}

        try:
            await asyncio.wait_for(proc.wait(), timeout=KILL_TIMEOUT)
            log.info("agent_terminated", agent_id=agent_id)
        except asyncio.TimeoutError:
            try:
                proc.kill()  # SIGKILL
                log.warning("agent_sigkill", agent_id=agent_id, pid=agent.pid)
                await proc.wait()
            except ProcessLookupError:
                pass

        self._cleanup(agent_id)
        return {"success": True, "agent_id": agent_id}

    def list_running(self) -> list[dict]:
        """Return list of running agents for heartbeat payload."""
        now = time.time()
        return [
            {
                "agent_id": a.agent_id,
                "pid": a.pid,
                "engine": a.engine,
                "uptime_seconds": int(now - a.started_at),
            }
            for a in self._agents.values()
        ]

    def get_running(self, agent_id: str) -> RunningAgent | None:
        """Return the RunningAgent for *agent_id*, or None if not running."""
        return self._agents.get(agent_id)

    def _cleanup(self, agent_id: str) -> None:
        """Delete temp profile file and remove from internal state."""
        agent = self._agents.pop(agent_id, None)
        if agent is None:
            return
        # Cancel watcher task if still running
        if agent.watch_task and not agent.watch_task.done():
            agent.watch_task.cancel()
        # Remove temp profile
        if agent.profile_path:
            agent.profile_path.unlink(missing_ok=True)
            log.debug("profile_cleaned", agent_id=agent_id, path=str(agent.profile_path))

    async def drain(self) -> None:
        """Kill all running agents (drain mode)."""
        agent_ids = list(self._agents.keys())
        for agent_id in agent_ids:
            await self.kill(agent_id)
        log.info("drain_complete", killed=len(agent_ids))
