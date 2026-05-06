"""Per-agent file manifest path validation (server-side).

This module mirrors :mod:`doorae_machine.agent_dir` so the server can
reject invalid paths at the REST API boundary, before they ever reach
the database or a ``spawn_agent`` frame. The two copies MUST stay in
sync — see ``docs/plans/2026-04-11-per-agent-directory-skills.md`` and
``docs/decisions/002-per-agent-directory-with-server-manifest.md`` for
the canonical rules.

Defense-in-depth: the machine also validates on materialize, so a
bypass at one layer alone is not sufficient to land an invalid path on
disk.
"""

from __future__ import annotations

from pathlib import PurePosixPath

_ALLOWED_PREFIXES: tuple[str, ...] = (
    "skills/",
    ".codex/",
    ".claude/",
    ".gemini/",
    ".openhands/",
)

# Issue #142 — project-root files that are admitted by exact
# match (not prefix). Kept small on purpose: each entry is a path
# engine CLIs look for at a specific location in the agent/project
# root. New entries should come with a concrete reason tied to an
# engine requirement.
_ALLOWED_EXACT_PATHS: frozenset[str] = frozenset({
    ".mcp.json",  # Claude Code 2.x project-local MCP registry
})

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md", ".json", ".toml", ".txt", ".yaml", ".yml", ".env",
        # Issue #112 — scripts that engine CLIs (Claude Code skills,
        # Codex / Gemini toolchains) legitimately invoke from
        # ``skills/<name>/scripts/*``. doorae does not execute these
        # itself: the materializer only writes them to the agent's
        # scratch dir and the engine's own sandbox is responsible
        # for execution policy. Write access stays admin-only.
        ".sh", ".py", ".js", ".ts", ".mjs",
    }
)

_MAX_DEPTH = 6
_MAX_PATH_LEN = 512


class AgentFilePathError(ValueError):
    """Raised when an agent_files row would have an invalid ``path``."""


def validate_agent_file_path(path: str) -> None:
    """Raise :class:`AgentFilePathError` if *path* is not allowed.

    Keep this in sync with
    ``doorae_machine.agent_dir.validate_agent_file_path``.
    """
    if not path:
        raise AgentFilePathError("path is empty")

    if len(path) > _MAX_PATH_LEN:
        raise AgentFilePathError(
            f"path longer than {_MAX_PATH_LEN} chars ({len(path)} given)"
        )

    if any(ord(ch) < 0x20 for ch in path):
        raise AgentFilePathError("path contains control/null character")

    if path.startswith("/"):
        raise AgentFilePathError("absolute paths are forbidden")

    if "\\" in path:
        raise AgentFilePathError("backslashes are forbidden")

    if "//" in path:
        raise AgentFilePathError("double slashes are forbidden")

    parts = path.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise AgentFilePathError(f"segment {part!r} is forbidden")

    if len(parts) > _MAX_DEPTH:
        raise AgentFilePathError(
            f"path deeper than {_MAX_DEPTH} segments ({len(parts)} given)"
        )

    name = PurePosixPath(path).name
    if name.startswith(".") and "." not in name[1:]:
        suffix = name
    else:
        suffix = PurePosixPath(path).suffix
    if suffix not in _ALLOWED_EXTENSIONS:
        raise AgentFilePathError(
            f"extension {suffix!r} is not in the allowed set"
        )

    if (
        not any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES)
        and path not in _ALLOWED_EXACT_PATHS
    ):
        raise AgentFilePathError(
            f"path must start with one of {_ALLOWED_PREFIXES} "
            f"or be an exact match of {sorted(_ALLOWED_EXACT_PATHS)}"
        )

    if parts[0] == "workspace":
        raise AgentFilePathError("workspace/ is runtime-only, not manifest")
