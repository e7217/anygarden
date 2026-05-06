"""Agent subprocess spawn/kill/watch manager."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

import structlog

from doorae_machine.agent_dir import (
    AgentFilePathError,
    validate_agent_file_path,
    validate_agent_id,
)
from doorae_machine.proc_kill import subprocess_group_kwargs, terminate_tree
from doorae_machine.safefs import safe_write_text, secure_chmod
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
    # Issue #237 — DB snapshot of the agent's long-term memory. The
    # spawner writes this to ``<agent_dir>/memory/notes.md`` if the file
    # doesn't yet exist, preserving the runtime file when it does (the
    # agent may have written between welcome frame and spawn reconcile).
    memory_md: str | None = None
    reasoning_effort: str | None = None
    model: str | None = None
    # Issue #309 — semantic permission tier ("restricted" |
    # "standard" | "trusted"). The spawner exports this as
    # ``DOORAE_AGENT_PERMISSION_LEVEL`` in the child env so each
    # engine adapter can resolve it into native dials. ``None``
    # means the adapter's "standard" mapping is used.
    permission_level: str | None = None
    sub_rooms: list[dict] = field(default_factory=list)
    # Issue #73 — which runtime process should host this agent.
    # ``"python"`` (default) spawns the existing Python ``doorae-agent``
    # binary; ``"typescript"`` spawns the Node ``doorae-agent-ts``
    # binary (falls back to ``npx -y @doorae/agent-ts`` when the local
    # bin isn't on PATH). Existing callers that don't set this field
    # continue to get the Python runtime — the dataclass default
    # guarantees backward compatibility.
    runtime: str = "python"
    # Issue #277 — bearer token for the doorae self-MCP entry that the
    # cluster baked into ``.codex/config.toml`` (codex) /
    # ``.mcp.json`` (claude-code) / ``.gemini/settings.json`` at frame
    # build time. Codex's ``[mcp_servers.doorae] bearer_token_env_var``
    # only resolves if the spawner exposes the token under that env
    # name in the agent process environment. ``None`` means the
    # cluster did NOT register the self-MCP entry (e.g.
    # ``cluster_external_url`` unset, or engine has no MCP support).
    doorae_mcp_token: str | None = None


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

    _PROTECTED_CLAUDE_DENY = (
        "Edit(.mcp.json)",
        "Write(.mcp.json)",
        "Edit(AGENTS.md)",
        "Write(AGENTS.md)",
        "Edit(CLAUDE.md)",
        "Write(CLAUDE.md)",
        "Edit(.claude/settings.json)",
        "Write(.claude/settings.json)",
        "Bash(rm:.mcp.json)",
        "Bash(rm:AGENTS.md)",
        "Bash(rm:CLAUDE.md)",
    )

    # Top-level entries owned by the materializer. These are wiped and
    # recreated on every spawn so stale manifest/config files cannot leak
    # into the next session. Everything else at agent_root is agent/user
    # runtime output and is preserved now that agent_root itself is cwd.
    # ``skills`` is also absent here: agents are allowed to improve or
    # add skills at runtime, so manifest skills are seeded without
    # clobbering existing files.
    # ``workspace`` is intentionally absent here: it is legacy runtime
    # output during migration, and a codex-only sandbox root after
    # materialize when codex lacks fine-grained read-only path support.
    _MATERIALIZER_MANAGED_TOP_LEVEL = frozenset({
        ".agents",
        ".claude",
        ".codex",
        ".gemini",
        ".mcp.json",
        "AGENTS.md",
        "CLAUDE.md",
    })

    _WORKSPACE_MANAGED_TOP_LEVEL = frozenset({
        ".doorae-codex-workspace",
        ".claude",
        "AGENTS.md",
        "CLAUDE.md",
        "memory",
        "skills",
    })
    _CODEX_WORKSPACE_MARKER = ".doorae-codex-workspace"

    # Default ``.claude/settings.json`` body for claude-code agents
    # whose admin manifest doesn't supply one. claude-agent-sdk loads
    # only project-scoped settings (``setting_sources=["project"]``),
    # so without this file every tool call gets denied by the SDK's
    # default ask-mode and there is no human in the loop to approve.
    # The trust model matches gemini-cli's ``--approval-mode yolo``
    # and codex's ``workspace-write`` mapping: tool calls are
    # permitted, while ``permissions.deny`` protects the materialized
    # instructions/config that live directly in the agent cwd. Admins
    # who want a tighter policy ship their own ``.claude/settings.json``
    # via the spawn manifest — that file is written first and the "is
    # the slot empty?" check below skips the default.
    #
    # Issue #309 — the allow-list now varies by ``permission_level``:
    # ``restricted`` agents lose Bash/Write/Edit/Task so the LLM can
    # only read and search; ``standard`` keeps the pre-#309 allow
    # list verbatim; ``trusted`` is identical to ``standard`` because
    # claude-code has no separate "host access" dial — Bash already
    # lets the agent shell out wherever the OS lets it. The mapping
    # is the canonical translation of the cluster's permission tier
    # for claude-code, and ``_claude_code_default_settings()`` is the
    # single call site every code path goes through.
    _CLAUDE_CODE_DEFAULT_SETTINGS = (
        '{\n'
        '  "permissions": {\n'
        '    "allow": [\n'
        '      "WebSearch",\n'
        '      "WebFetch",\n'
        '      "Bash",\n'
        '      "Read",\n'
        '      "Write",\n'
        '      "Edit",\n'
        '      "Glob",\n'
        '      "Grep",\n'
        '      "Task",\n'
        '      "TodoWrite"\n'
        '    ],\n'
        '    "deny": [\n'
        '      "Edit(.mcp.json)",\n'
        '      "Write(.mcp.json)",\n'
        '      "Edit(AGENTS.md)",\n'
        '      "Write(AGENTS.md)",\n'
        '      "Edit(CLAUDE.md)",\n'
        '      "Write(CLAUDE.md)",\n'
        '      "Edit(.claude/settings.json)",\n'
        '      "Write(.claude/settings.json)",\n'
        '      "Bash(rm:.mcp.json)",\n'
        '      "Bash(rm:AGENTS.md)",\n'
        '      "Bash(rm:CLAUDE.md)"\n'
        '    ]\n'
        '  }\n'
        '}\n'
    )

    _CLAUDE_CODE_RESTRICTED_SETTINGS = (
        '{\n'
        '  "permissions": {\n'
        '    "allow": [\n'
        '      "WebSearch",\n'
        '      "WebFetch",\n'
        '      "Read",\n'
        '      "Glob",\n'
        '      "Grep"\n'
        '    ],\n'
        '    "deny": [\n'
        '      "Edit(.mcp.json)",\n'
        '      "Write(.mcp.json)",\n'
        '      "Edit(AGENTS.md)",\n'
        '      "Write(AGENTS.md)",\n'
        '      "Edit(CLAUDE.md)",\n'
        '      "Write(CLAUDE.md)",\n'
        '      "Edit(.claude/settings.json)",\n'
        '      "Write(.claude/settings.json)",\n'
        '      "Bash(rm:.mcp.json)",\n'
        '      "Bash(rm:AGENTS.md)",\n'
        '      "Bash(rm:CLAUDE.md)"\n'
        '    ]\n'
        '  }\n'
        '}\n'
    )

    @classmethod
    def _claude_code_default_settings(
        cls, permission_level: str | None
    ) -> str:
        """Return the JSON body to materialize at
        ``.claude/settings.json`` when the admin didn't ship one.

        ``restricted`` strips Bash/Write/Edit/Task so the agent can
        only inspect files. ``standard``/``trusted``/None keep the
        pre-#309 broad allow-list. ``ValueError`` on unknown tiers
        — same fail-loud contract as the codex/gemini mappings.
        """
        if permission_level is None or permission_level in (
            "standard",
            "trusted",
        ):
            return cls._CLAUDE_CODE_DEFAULT_SETTINGS
        if permission_level == "restricted":
            return cls._CLAUDE_CODE_RESTRICTED_SETTINGS
        raise ValueError(
            f"unknown permission_level: {permission_level!r} — "
            "expected one of ('restricted', 'standard', 'trusted')"
        )

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

        # ── Memory auto-inline (#237 file-memory convention) ────
        sections.append("")
        sections.append("## Memory")
        sections.append("")
        sections.append(
            "You have a long-term memory file at `memory/notes.md` "
            "(relative to your current working directory). The cluster "
            "also injects the "
            "current contents into your `system_prompt` at session "
            "start, so treat it as a shared notebook between sessions.\n\n"
            "Guidelines:\n"
            "- Append (do not overwrite) observations you want to "
            "remember across sessions. Examples: user preferences, "
            "ongoing project state, important facts.\n"
            "- Use markdown sections so the file stays scannable.\n"
            "- If the file grows too long, prune or summarise entries "
            "yourself — there is no automatic rollover.\n"
            "- When a session is marked **ephemeral** "
            "(`<ephemeral-session/>` in your system prompt), do NOT "
            "write to this file. The user expects the conversation to "
            "leave no trace in long-term memory.\n\n"
            "The machine syncs this file back to the cluster DB "
            "periodically and on shutdown, so writes survive restart "
            "and machine migration."
        )

        # ── Outbox / artifacts (#290) ────────────────────────────
        sections.append("")
        sections.append("## Sharing artifacts with the user")
        sections.append("")
        sections.append(
            "When you want to show the user an image, screenshot, "
            "chart, log dump, or other file that won't fit cleanly "
            "in a chat message, drop the file into `memory/outbox/` "
            "(relative to your agent directory). The machine watches "
            "this folder and pushes new files to the room's right-hand "
            "*Artifacts* panel where the user can preview and "
            "download them.\n\n"
            "Constraints:\n"
            "- One file per artifact, ≤ 768 KiB each.\n"
            "- Allowed types: PNG / JPEG / GIF / WebP / SVG images, "
            "and the same text/markdown/json/yaml/csv MIMEs the room "
            "shared-files flow accepts.\n"
            "- Use a descriptive filename — it's what the user sees "
            "in the panel and on download.\n"
            "- Files surface in *every* room you're a participant of "
            "at the time you write them; same-content re-writes are "
            "deduped server-side."
        )

        # Only add trailing newline if we appended extra sections.
        if len(sections) > 1:
            sections.append("")
        return "\n".join(sections)

    @staticmethod
    def _remove_tree_entry(path: Path) -> None:
        """Remove a path owned by the materializer without following symlinks."""
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    @classmethod
    def _migrate_legacy_workspace(cls, agent_root: Path, engine: str) -> None:
        """Move old workspace output into agent_root where safe.

        The old layout used ``workspace/`` as the process cwd and also
        placed bridge files there. On upgrade, discard bridge/config slots
        and move user/runtime output up to the new cwd only when no target
        already exists. Conflicts are left under workspace rather than
        overwritten.
        """
        workspace = agent_root / "workspace"
        if not workspace.is_dir() or workspace.is_symlink():
            return
        marker = workspace / cls._CODEX_WORKSPACE_MARKER
        if marker.is_file() and engine == "codex":
            return

        for entry in list(workspace.iterdir()):
            if entry.name in cls._WORKSPACE_MANAGED_TOP_LEVEL:
                try:
                    cls._remove_tree_entry(entry)
                except OSError:
                    log.warning("workspace_bridge_cleanup_failed", path=str(entry))
                continue

            target_name = "MEMORY.md" if entry.name == "MEMORY.md" else entry.name
            target = agent_root / target_name
            if target.exists() or target.is_symlink():
                log.warning(
                    "workspace_migration_conflict",
                    source=str(entry),
                    target=str(target),
                )
                continue
            shutil.move(str(entry), str(target))

        try:
            workspace.rmdir()
        except OSError:
            log.warning("workspace_migration_leftover", path=str(workspace))

    @classmethod
    def _prune_materializer_managed_entries(cls, agent_root: Path) -> None:
        """Drop stale managed entries while preserving agent-created output."""
        for name in cls._MATERIALIZER_MANAGED_TOP_LEVEL:
            entry = agent_root / name
            if not entry.exists() and not entry.is_symlink():
                continue
            try:
                cls._remove_tree_entry(entry)
            except OSError as exc:
                raise RuntimeError(
                    f"Failed to prune {entry} during materialize: {exc}"
                ) from exc

    @classmethod
    def _ensure_real_directory(cls, path: Path, *, mode: int = 0o700) -> None:
        """Ensure ``path`` is a real directory owned by this materializer.

        Agent-owned content can live *inside* the directory, but the
        directory itself must not be a symlink. Otherwise a manifest seed
        could write outside the agent root through a parent symlink.
        """
        if path.is_symlink() or path.is_file():
            cls._remove_tree_entry(path)
        elif path.exists() and not path.is_dir():
            cls._remove_tree_entry(path)
        path.mkdir(parents=True, exist_ok=True)
        secure_chmod(path, mode)

    @staticmethod
    def _has_symlink_parent(root: Path, target: Path) -> bool:
        """Return true if any parent between ``root`` and ``target`` is a symlink."""
        try:
            relative = target.relative_to(root)
        except ValueError:
            return True

        current = root
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                return True
        return False

    @classmethod
    def _seed_manifest_skill_files(
        cls,
        agent_root: Path,
        files: dict[str, str],
    ) -> None:
        """Seed missing manifest skills without clobbering agent edits."""
        skills_root = agent_root / "skills"
        for rel_path, content in files.items():
            if not rel_path.startswith("skills/"):
                continue

            target = agent_root / rel_path
            if target.exists() or target.is_symlink():
                continue
            if cls._has_symlink_parent(skills_root, target):
                log.warning(
                    "skill_seed_skipped_symlink_parent",
                    path=str(target),
                )
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            secure_chmod(target.parent, 0o700)
            safe_write_text(target, content, mode=0o600)

    def _materialize_agent_dir(self, msg: SpawnManifest) -> Path:
        """Reconcile the on-disk agent directory with the spawn manifest.

        Builds ``<agent_dirs_root>/<agent_id>/`` so that after this call:

        - ``AGENTS.md`` mirrors ``msg.agents_md`` (absent if ``None``)
        - every entry in ``msg.files`` exists at that relative path
          with mode 0o600
        - engine-convention symlinks (``CLAUDE.md`` → ``AGENTS.md``,
          ``.agents/skills``/``.claude/skills`` → ``../skills``) are
          fresh
        - materializer-owned config/instruction paths are refreshed
        - agent-created output directly under the agent root is preserved
          because the agent root is now the process cwd

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
        secure_chmod(agent_root, 0o700)

        self._migrate_legacy_workspace(agent_root, msg.engine)
        self._prune_materializer_managed_entries(agent_root)

        # --- Write AGENTS.md from manifest -----------------------------
        #
        # Content is composed by _compose_agents_md: base agents_md
        # body plus an auto-inlined "## Available skills" section
        # carrying every skills/*/SKILL.md body. See the helper's
        # docstring for why codex needs this and why claude-code /
        # gemini-cli tolerate the extra content.
        if msg.agents_md is not None:
            agents_md = agent_root / "AGENTS.md"
            safe_write_text(agents_md, self._compose_agents_md(msg), mode=0o600)

        # --- Write memory/notes.md (#237) ------------------------------
        #
        # Direction: DB snapshot → file. ``memory/`` now sits directly
        # under the agent cwd, so shared/outbox contents survive the
        # managed-prune pass while notes.md is refreshed from the
        # cluster's last-known snapshot.
        memory_dir = agent_root / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        secure_chmod(memory_dir, 0o700)
        notes_path = memory_dir / "notes.md"
        safe_write_text(notes_path, msg.memory_md or "", mode=0o600)
        # #246 — ``memory/shared/`` is the drop zone for room-shared
        # files pushed by the server. Pre-create it so the daemon's
        # write handler doesn't have to special-case first delivery
        # (and so agents enumerate an empty dir instead of a missing
        # one when no files have been shared yet).
        shared_dir = memory_dir / "shared"
        shared_dir.mkdir(parents=True, exist_ok=True)
        secure_chmod(shared_dir, 0o700)
        # #290 — symmetrical drop zone for the outbound flow: the
        # agent writes a file here and the daemon's outbox poller
        # ships it back to the cluster as a room artifact. Pre-creating
        # the directory lets the agent treat it as always-present.
        outbox_dir = memory_dir / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        secure_chmod(outbox_dir, 0o700)

        # --- Writable skills directory --------------------------------
        #
        # Skills are agent-owned runtime content. The manifest can seed
        # missing skills, but a respawn must not clobber edits or
        # agent-authored skills. Keep the root as a real directory so
        # materializer writes cannot escape via a symlink parent.
        skills_dir = agent_root / "skills"
        self._ensure_real_directory(skills_dir)

        # --- Write each file in the manifest ---------------------------
        for rel_path, content in msg.files.items():
            if rel_path.startswith("skills/"):
                continue
            target = agent_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            secure_chmod(target.parent, 0o700)
            safe_write_text(target, content, mode=0o600)
        self._seed_manifest_skill_files(agent_root, msg.files)

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
        # and Claude Code both find the canonical skill directory). The
        # canonical directory is always present because agents may add
        # skills at runtime even when the manifest seeds none.
        for alias_dir, target_rel in (
            (agent_root / ".agents" / "skills", "../skills"),
            (agent_root / ".claude" / "skills", "../skills"),
        ):
            alias_dir.parent.mkdir(parents=True, exist_ok=True)
            secure_chmod(alias_dir.parent, 0o700)
            if alias_dir.exists() or alias_dir.is_symlink():
                if alias_dir.is_symlink():
                    alias_dir.unlink()
                else:
                    shutil.rmtree(alias_dir)
            alias_dir.symlink_to(target_rel)

        # --- Default .claude/settings.json for claude-code ------------
        # Issue #111. The admin-supplied file (if any) was already
        # written by the manifest loop above, so the existence check
        # here is the override mechanism: present → admin wins, absent
        # → fall back to the permissive default that lets the agent
        # actually use its tools. See ``_CLAUDE_CODE_DEFAULT_SETTINGS``
        # for the trust-model rationale.
        if msg.engine == "claude-code":
            settings_path = agent_root / ".claude" / "settings.json"
            if not settings_path.exists():
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                secure_chmod(settings_path.parent, 0o700)
                # #309 — pick the allow-list that matches the agent's
                # permission tier. Admin-supplied settings still win
                # via the ``settings_path.exists()`` short-circuit
                # above; this default is only used when the manifest
                # left the slot empty.
                safe_write_text(
                    settings_path,
                    self._claude_code_default_settings(msg.permission_level),
                    mode=0o600,
                )

        # --- Symlink host codex auth into per-agent CODEX_HOME --------
        # When ``Spawner.spawn`` is about to redirect ``CODEX_HOME`` at
        # ``<agent_root>/.codex/`` (triggered by a ``.codex/*`` overlay
        # on the manifest — MCP template or admin config), codex no
        # longer reads the host user's ``~/.codex/auth.json`` that
        # carries the ChatGPT-login / OAuth tokens. Deployments that
        # rely on ``codex auth login`` (i.e. do NOT supply
        # ``OPENAI_API_KEY`` via ``engine_secrets``) would then fail to
        # authenticate at first turn — the task starts but the LLM
        # call silently returns empty and the agent appears stuck.
        #
        # Symlinking the host auth.json into the per-agent codex home
        # restores pre-#213 auth discovery semantics while keeping the
        # per-agent config overlay and session isolation. Admin-
        # authored ``.codex/auth.json`` in the manifest still wins
        # because the file-write loop already ran, so
        # ``per_agent_auth.exists()`` short-circuits the symlink.
        #
        # Multi-agent deployments end up sharing the host auth token
        # via N symlinks pointing at the same file, which mirrors the
        # pre-#213 behaviour where every codex agent read
        # ``~/.codex/auth.json`` directly — no regression.
        has_codex_overlay = any(
            path.startswith(".codex/") for path in msg.files
        )
        if msg.engine == "codex" and has_codex_overlay:
            per_agent_auth = agent_root / ".codex" / "auth.json"
            host_auth = Path.home() / ".codex" / "auth.json"
            if (
                host_auth.is_file()
                and not per_agent_auth.is_symlink()
                and not per_agent_auth.exists()
            ):
                per_agent_auth.symlink_to(host_auth)

        # engine_secrets is NOT rendered to disk — it flows into the
        # subprocess environment via ``Spawner.spawn`` (#184). Writing
        # an ``.env`` file here would re-expose the plaintext keys to
        # the agent's Read tool since the agent sandbox can reach the
        # engine config dir via cwd traversal.

        # --- Seed root MEMORY.md if absent ------------------------------
        memory_md = agent_root / "MEMORY.md"
        if not memory_md.exists() and not memory_md.is_symlink():
            safe_write_text(
                memory_md,
                "# Memory\n\nNo prior context. This is the first session.\n",
                mode=0o600,
            )

        # --- Codex workspace-write fallback -----------------------------
        #
        # Issue #345 originally planned to express codex self-protection
        # with ``read_only_paths`` while running from agent_root. The
        # installed codex-cli 0.128.0 / codex-python protocol exposes
        # ``writable_roots`` but no read-only path exceptions, so making
        # agent_root the codex workspace would let a standard codex agent
        # rewrite AGENTS.md, .mcp.json, or skills during the current
        # session. Keep the machine-level doorae-agent cwd collapsed to
        # agent_root, but give the codex SDK thread a narrow workspace
        # child with read bridges back to managed context. The codex
        # adapter pins ThreadStartOptions.cwd at this directory.
        if msg.engine == "codex":
            workspace = agent_root / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            secure_chmod(workspace, 0o700)
            safe_write_text(
                workspace / self._CODEX_WORKSPACE_MARKER,
                "codex workspace-write sandbox root\n",
                mode=0o600,
            )

            composed = self._compose_agents_md(msg) if msg.agents_md is not None else None
            for slot_name in ("AGENTS.md", "CLAUDE.md"):
                slot = workspace / slot_name
                if slot.is_symlink() or slot.exists():
                    self._remove_tree_entry(slot)
                if composed is not None:
                    slot.symlink_to(f"../{slot_name}")

            ws_memory = workspace / "memory"
            ws_memory.mkdir(parents=True, exist_ok=True)
            secure_chmod(ws_memory, 0o700)
            for name in ("notes.md", "shared", "outbox"):
                slot = ws_memory / name
                if slot.is_symlink() or slot.exists():
                    self._remove_tree_entry(slot)
                slot.symlink_to(f"../../memory/{name}")

            ws_skills = workspace / "skills"
            if ws_skills.is_symlink() or ws_skills.exists():
                self._remove_tree_entry(ws_skills)
            ws_skills.symlink_to("../skills")

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
            secure_chmod(profile_path, 0o600)
        except OSError as exc:
            return SpawnResult(
                success=False,
                agent_id=agent_id,
                error=f"Failed to write profile: {exc}",
            )

        # Build environment: inherit current env + set DOORAE_TOKEN.
        #
        # ``engine_secrets`` are deliberately NOT merged into this env.
        # ``doorae-agent`` would inherit them and an LLM tool call
        # inside the agent (Bash, Read) could then exfiltrate every
        # API key by dumping ``/proc/self/environ`` or running ``env``.
        # Instead the secrets are piped via stdin below and consumed
        # by ``doorae_agent.secrets.load_from_stdin()`` at startup —
        # the agent process's /proc/self/environ stays clean (#184).
        #
        # ``DOORAE_TOKEN`` stays in env because the agent's auth
        # identity token is a single doorae-internal credential with
        # a much smaller blast radius than third-party API keys, and
        # the existing agent bootstrap reads it from env via
        # ``load_token``.
        env = os.environ.copy()
        env["DOORAE_TOKEN"] = msg.agent_token

        # Issue #277 — codex's streamable HTTP MCP form references the
        # token by env-var name (``bearer_token_env_var``) instead of
        # storing it in ``.codex/config.toml``. Inject the matching
        # variable so codex can resolve the doorae self-MCP entry at
        # tool-call time. claude-code / gemini-cli already see the
        # token as a literal Authorization header in their settings
        # file, so this env var is effectively a no-op for them — but
        # we still set it unconditionally to keep the spawn contract
        # symmetric across engines.
        if msg.doorae_mcp_token:
            env["DOORAE_AGENT_TOKEN"] = msg.doorae_mcp_token

        # Issue #309 — semantic permission tier. The agent process's
        # engine adapter resolves this into native dials (codex
        # ``sandbox``+``approval_policy``; gemini ``--approval-mode``;
        # claude-code allow-list). ``None`` is treated as "standard"
        # by the adapter so the env var is set unconditionally —
        # adapters can rely on its presence and don't have to
        # special-case the missing key.
        env["DOORAE_AGENT_PERMISSION_LEVEL"] = msg.permission_level or "standard"

        # Redirect ``CODEX_HOME`` at the per-agent ``.codex/`` ONLY when
        # the manifest actually carries a codex overlay (MCP templates
        # or admin-authored ``.codex/config.toml``). Codex resolves its
        # config exclusively from ``$CODEX_HOME/config.toml`` and does
        # NOT walk cwd for a project-local ``.codex/`` the way
        # claude-code (``.mcp.json``) and gemini-cli (``.gemini/``) do,
        # so without this redirect the MCP overlay is silently ignored.
        #
        # BUT unconditional redirection regresses the supported
        # host-auth startup path: a codex agent with no overlay and no
        # ``engine_secrets`` API key relies on the host user's
        # ``~/.codex/auth.json`` (codex ChatGPT login) plus any
        # host-level config. Pointing ``CODEX_HOME`` at an empty
        # per-agent ``.codex/`` strips both and makes the agent fail
        # to authenticate at first-turn time. Scoping the redirect to
        # "overlay present" preserves host-auth for agents that never
        # needed per-agent config in the first place, while agents
        # that opt into MCP implicitly also opt into per-agent auth
        # (typically via ``engine_secrets``/LLM gateway — the usual
        # doorae model for MCP-enabled agents).
        has_codex_overlay = any(
            path.startswith(".codex/") for path in msg.files
        )
        if (
            msg.engine == "codex"
            and agent_root is not None
            and has_codex_overlay
        ):
            env["CODEX_HOME"] = str(agent_root / ".codex")

        # The daemon's own server URL is authoritative — it's the address the
        # daemon is connected to right now, so it's guaranteed reachable from
        # this host. Fall back to the frame-supplied URL only if the daemon
        # didn't provide one (older versions).
        agent_server = self._agent_server_url or msg.server_url

        # Build command. Branch on ``msg.runtime``:
        #   "python"     → local doorae-agent (PyPI) with uvx fallback.
        #   "typescript" → local doorae-agent-ts (npm) with
        #                   ``npx -y @doorae/agent-ts`` fallback.
        #
        # Log which source was picked so operators can later answer
        # the "which binary actually ran?" question without rebuilding
        # the environment. Two different spawns on the same machine
        # can end up with different binaries (PATH shadowing, uvx/npx
        # cache drift) and the log is our only forensic trail.
        runtime = msg.runtime or "python"
        if runtime == "typescript":
            agent_name = msg.name or f"agent-{agent_id[:8]}"
            doorae_agent_ts = shutil.which("doorae-agent-ts")
            if doorae_agent_ts:
                cmd = [
                    doorae_agent_ts,
                    "--engine", msg.engine,
                    "--name", agent_name,
                    "--server", agent_server,
                ]
                log.info(
                    "agent_binary_resolved",
                    agent_id=agent_id,
                    runtime="typescript",
                    source="path",
                    path=doorae_agent_ts,
                )
            else:
                cmd = [
                    "npx",
                    "-y",
                    "@doorae/agent-ts",
                    "--engine", msg.engine,
                    "--name", agent_name,
                    "--server", agent_server,
                ]
                log.info(
                    "agent_binary_resolved",
                    agent_id=agent_id,
                    runtime="typescript",
                    source="npx",
                    path=None,
                )
        else:
            # Default Python runtime — unchanged from pre-#73 behaviour.
            doorae_agent = shutil.which("doorae-agent")
            if doorae_agent:
                cmd = [
                    doorae_agent,
                    "--engine", msg.engine,
                    "--name", msg.name or f"agent-{agent_id[:8]}",
                    "--server", agent_server,
                ]
                log.info(
                    "agent_binary_resolved",
                    agent_id=agent_id,
                    runtime="python",
                    source="path",
                    path=doorae_agent,
                )
            else:
                # doorae-agent not in PATH — use uvx to fetch from PyPI
                cmd = [
                    "uvx",
                    "doorae-agent",
                    "--engine", msg.engine,
                    "--name", msg.name or f"agent-{agent_id[:8]}",
                    "--server", agent_server,
                ]
                log.info(
                    "agent_binary_resolved",
                    agent_id=agent_id,
                    runtime="python",
                    source="uvx",
                    path=None,
                )
        if msg.profile_yaml.strip():
            cmd.extend(["--profile", str(profile_path)])
        for room in msg.rooms:
            cmd.extend(["--room", room])
        if msg.reasoning_effort:
            cmd.extend(["--reasoning-effort", msg.reasoning_effort])
        if msg.model:
            cmd.extend(["--model", msg.model])

        # Spawn the subprocess with cwd set to the canonical agent root.
        # Instructions, engine config, memory/shared, and memory/outbox
        # now live directly under this directory, so engines no longer
        # need workspace/ bridge files to discover project context.
        agent_cwd = str(agent_root) if agent_root else None

        # Pipe ``msg.engine_secrets`` to the agent via stdin. The agent
        # reads the JSON payload once at startup (``doorae_agent.secrets
        # .load_from_stdin``) and stores it in a private module rather
        # than ``os.environ``. Closing stdin after the write signals EOF
        # so the agent's ``sys.stdin.read`` returns cleanly. Empty dicts
        # still get piped (as ``"{}"``) so the agent always runs the
        # same bootstrap path.
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                cwd=agent_cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Put the agent in its own process group so terminate_tree
                # can reliably reach grandchildren (e.g. shells, runtime
                # workers) on both POSIX and Windows.
                **subprocess_group_kwargs(),
            )
        except OSError as exc:
            profile_path.unlink(missing_ok=True)
            return SpawnResult(
                success=False,
                agent_id=agent_id,
                error=f"Failed to start process: {exc}",
            )

        secrets_payload = json.dumps(dict(msg.engine_secrets or {})).encode("utf-8")
        try:
            assert proc.stdin is not None  # PIPE guarantees it
            proc.stdin.write(secrets_payload)
            await proc.stdin.drain()
            proc.stdin.close()
            try:
                await proc.stdin.wait_closed()
            except AttributeError:
                # Python <3.11 asyncio has no wait_closed on StreamWriter;
                # the close() above is best-effort in that case.
                pass
        except (BrokenPipeError, ConnectionResetError) as exc:
            # Agent died before consuming stdin. Let the watch task
            # surface the exit code; don't mask the real failure here.
            log.warning(
                "secrets_stdin_pipe_broken",
                agent_id=agent_id,
                error=str(exc),
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
        """Kill a running agent: terminate-tree (graceful) -> kill survivors.

        Goes through ``proc_kill.terminate_tree`` so the whole process
        group (agent + any child shells / language runtimes it
        spawned) is reaped together. On POSIX this maps to
        SIGTERM → SIGKILL; on Windows to TerminateProcess on the new
        process group.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            return {"success": False, "error": f"Agent {agent_id} not found"}

        proc = agent.proc
        if proc.returncode is not None:
            self._cleanup(agent_id)
            return {"success": True, "note": "Process already exited"}

        log.info("agent_terminate_tree", agent_id=agent_id, pid=agent.pid)
        await asyncio.to_thread(terminate_tree, agent.pid, timeout=KILL_TIMEOUT)
        try:
            # Drain the asyncio.subprocess state machine; the process
            # is already dead by now, so this returns immediately.
            await proc.wait()
        except ProcessLookupError:
            pass
        log.info("agent_terminated", agent_id=agent_id)

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

    def get_agent_root(self, agent_id: str) -> Path:
        """Return the per-agent directory for *agent_id*.

        Issue #237 — exposed so the daemon can read ``memory/notes.md``
        without recomputing the path. Validation lives in
        ``_materialize_agent_dir`` (the only writer); this read accessor
        trusts the agent_id has already been validated by the spawn
        path that placed the directory.
        """
        return self._agent_dirs_root / agent_id

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
