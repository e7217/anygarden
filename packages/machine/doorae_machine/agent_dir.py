"""Per-agent directory path validation.

Shared whitelist rules for files that the server manifest (via
``spawn_agent.files``) is allowed to materialize under an agent's
directory on disk. The same rules must be enforced server-side when
rows go into ``agent_files``, but this module is the machine-side
defense-in-depth layer.

Goals:

- **No agent-id escape**: ``msg.agent_id`` is used as a path segment
  under ``~/.doorae/agents/``, so a malicious server that sends
  ``agent_id="../other"`` or ``agent_id="/etc"`` would have
  ``Path(root) / agent_id`` escape the managed root. A narrow
  filename-like regex makes this impossible at the source.
- **No path escape**: absolute paths, ``..`` traversal, symlink-expressed
  paths, and control characters are rejected.
- **No workspace clobber**: ``workspace/`` is the agent's runtime
  scratch and must not be writable from the manifest, otherwise the
  server could overwrite state the agent was relying on between
  spawns.
- **No synthetic file collisions**: ``AGENTS.md`` is written by the
  materializer from ``spawn_agent.agents_md``, and ``CLAUDE.md`` is a
  synthetic symlink the materializer creates — neither can come
  through the ``files`` map.
- **No executables**: only text-ish configuration extensions are
  allowed, so a compromised admin account can't smuggle a binary or
  shell script onto the host.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_ALLOWED_PREFIXES: tuple[str, ...] = (
    "skills/",
    ".codex/",
    ".claude/",
    ".gemini/",
    ".openhands/",
)

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".json", ".toml", ".txt", ".yaml", ".yml", ".env"}
)

# Cap path depth so a manifest can't force the materializer into
# unbounded mkdir trees. Six segments (e.g.
# skills/a/b/c/refs/deep.md) is plenty for realistic skill layouts.
_MAX_DEPTH = 6

# Bound total length to keep pathological inputs from ballooning
# filesystem syscalls.
_MAX_PATH_LEN = 512

# ``agent_id`` is used as a path segment, so it has to be safe to
# concatenate with a directory. Doorae itself generates UUID4 strings
# (36 chars, ``[0-9a-f-]``), but we allow a slightly wider alphabet
# so test fixtures like ``agent-x`` also fit. No dots (which would
# let ``.`` and ``..`` through), no slashes, no control characters.
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AgentFilePathError(ValueError):
    """Raised when a manifest path does not meet the whitelist rules."""


def validate_agent_id(agent_id: str) -> None:
    """Raise :class:`AgentFilePathError` if *agent_id* is not safe to
    use as a directory name under the agent-dir root.

    This is a critical defense: ``Path(root) / agent_id`` does not
    protect against absolute paths (``/etc`` clobbers the join) or
    ``..`` traversal (``../other`` escapes). Reject both by requiring
    a narrow filename-like alphabet.
    """
    if not isinstance(agent_id, str):
        raise AgentFilePathError(
            f"agent_id must be a string, got {type(agent_id).__name__}"
        )
    if not _AGENT_ID_RE.match(agent_id):
        raise AgentFilePathError(
            f"agent_id {agent_id!r} must match {_AGENT_ID_RE.pattern}"
        )


def validate_agent_file_path(path: str) -> None:
    """Raise :class:`AgentFilePathError` if *path* is not an allowed
    agent-manifest path.

    Rules mirror the plan doc and ADR-002 — keep in sync with the
    server-side copy in ``doorae_server/agent_files.py``.
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

    # Break into POSIX parts after the checks above so PurePosixPath
    # cannot normalize away segments we care about rejecting.
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise AgentFilePathError(f"segment {part!r} is forbidden")

    if len(parts) > _MAX_DEPTH:
        raise AgentFilePathError(
            f"path deeper than {_MAX_DEPTH} segments ({len(parts)} given)"
        )

    # Extension check. Leading-dot filenames (e.g. ``.env``) are
    # ``PurePosixPath(...).suffix == ""`` because Python treats the
    # whole basename as an extension marker, so we read the name
    # directly for those.
    name = PurePosixPath(path).name
    if name.startswith(".") and "." not in name[1:]:
        suffix = name
    else:
        suffix = PurePosixPath(path).suffix
    if suffix not in _ALLOWED_EXTENSIONS:
        raise AgentFilePathError(
            f"extension {suffix!r} is not in the allowed set"
        )

    if not any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
        raise AgentFilePathError(
            f"path must start with one of {_ALLOWED_PREFIXES}"
        )

    # workspace/ is the runtime scratch for the agent and must never
    # be clobbered by a manifest write. The prefix list above already
    # excludes it, but we assert explicitly to keep this rule obvious
    # when someone adds a future prefix.
    if parts[0] == "workspace":
        raise AgentFilePathError("workspace/ is runtime-only, not manifest")
