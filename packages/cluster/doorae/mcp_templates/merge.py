"""Engine-specific MCP config rendering + manifest file merging (#124).

Pure functions. No DB access. The service layer feeds already-resolved
instances into these and takes back the final manifest strings —
keeping I/O and rendering in separate layers makes each easily
testable in isolation.

Engine formats:

- **claude-code**: ``.claude/settings.json``, shape
  ``{"mcpServers": {<name>: {command, args, env}}}``.
- **codex**: ``.codex/config.toml``, shape
  ``[mcp_servers.<name>] command = ... args = [...] [mcp_servers.<name>.env] ...``.
- **gemini-cli**: ``.gemini/settings.json``, same JSON shape as
  claude-code.

Precedence when an admin-authored manifest file already exists:
the admin's mcpServers entries win on key collision so an admin can
explicitly override a builtin template's config. Admin keys without
matching overlay keys are preserved untouched (so permissions.allow
etc. stay intact).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass

import tomli_w


# ── Engine → file path ────────────────────────────────────────────

CLAUDE_SETTINGS_PATH = ".claude/settings.json"
CODEX_CONFIG_PATH = ".codex/config.toml"
GEMINI_SETTINGS_PATH = ".gemini/settings.json"


def settings_path_for_engine(engine: str) -> str | None:
    """Return the per-agent settings file path for ``engine``, or None.

    Returns ``None`` for engines that don't support MCP (``openai``,
    ``anthropic``, ``echo``) so the caller can skip rendering without
    a guard at every callsite.
    """
    return {
        "claude-code": CLAUDE_SETTINGS_PATH,
        "codex": CODEX_CONFIG_PATH,
        "gemini-cli": GEMINI_SETTINGS_PATH,
    }.get(engine)


# ── Env placeholder interpolation ────────────────────────────────


_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def substitute_env_placeholders(
    config: dict | list | str,
    env_values: dict[str, str],
) -> dict | list | str:
    """Recursively replace ``${VAR}`` with ``env_values[VAR]``.

    Unresolved placeholders stay as-is — the CLI engines typically
    either error out or fall through to os.environ, which is useful
    for edge cases like the ``filesystem`` builtin's
    ``MCP_FS_ALLOWED_PATH`` where the admin may prefer to set the
    value on the agent environment rather than store it encrypted.
    """
    if isinstance(config, str):
        def _sub(match: re.Match[str]) -> str:
            var = match.group(1)
            return env_values.get(var, match.group(0))

        return _PLACEHOLDER_RE.sub(_sub, config)
    if isinstance(config, list):
        return [substitute_env_placeholders(item, env_values) for item in config]
    if isinstance(config, dict):
        return {
            key: substitute_env_placeholders(value, env_values)
            for key, value in config.items()
        }
    return config


# ── Instance → rendered config ───────────────────────────────────


@dataclass(frozen=True)
class RenderedInstance:
    """Output of :func:`render_instance`.

    Keeps the template name alongside the rendered config so the
    merge step can key by name without carrying the template row.
    """

    name: str
    config: dict


def render_instance(
    *,
    name: str,
    config_per_engine: dict[str, dict],
    env_values: dict[str, str],
    engine: str,
) -> RenderedInstance | None:
    """Pick the engine-specific block, interpolate env, return rendered.

    Returns ``None`` when the template has no config for the target
    engine — the service layer filters unsupported attachments at
    attach time, so this is a defensive guard that lets spawn-time
    rendering silently skip a mismatched row instead of crashing the
    whole frame build.
    """
    block = config_per_engine.get(engine)
    if block is None:
        return None
    rendered = substitute_env_placeholders(block, env_values)
    if not isinstance(rendered, dict):
        # Malformed config — drop rather than poisoning the manifest.
        return None
    return RenderedInstance(name=name, config=rendered)


# ── JSON settings merge (claude-code + gemini-cli) ───────────────


def merge_json_settings(
    admin_json: str | None,
    overlays: list[RenderedInstance],
) -> str:
    """Merge template overlays into an admin-authored JSON settings file.

    - If ``admin_json`` is empty / None, start from ``{}``.
    - Overlays contribute ``mcpServers.<name>`` entries.
    - Admin keys under ``mcpServers`` win on name collision (admin
      override respected), otherwise overlay fills in the gap.
    - All other admin keys (``permissions``, ``env``, etc.) are
      preserved verbatim.

    Returns pretty-printed JSON so the file remains human-editable
    after the overlay lands.
    """
    if admin_json and admin_json.strip():
        try:
            base: dict = json.loads(admin_json)
            if not isinstance(base, dict):
                base = {}
        except json.JSONDecodeError:
            # Admin produced malformed JSON — leave the file alone
            # rather than silently overwriting their content. The
            # engine will complain on spawn, which is the right place
            # for the admin to discover the bug.
            return admin_json
    else:
        base = {}

    existing_servers = base.get("mcpServers")
    if not isinstance(existing_servers, dict):
        existing_servers = {}
    merged_servers = dict(existing_servers)
    for overlay in overlays:
        merged_servers.setdefault(overlay.name, overlay.config)
    base["mcpServers"] = merged_servers

    return json.dumps(base, indent=2, ensure_ascii=False) + "\n"


# ── TOML config merge (codex) ────────────────────────────────────


def merge_codex_config(
    admin_toml: str | None,
    overlays: list[RenderedInstance],
) -> str:
    """Merge overlays into codex ``config.toml``.

    Strategy: parse existing TOML into a dict, augment its
    ``mcp_servers`` table with overlay entries (admin wins on name
    collision), round-trip via ``tomli_w`` so formatting stays
    readable. Doesn't try to preserve admin comments — TOML merge
    that retains comments would need a full AST-preserving writer,
    which is out of scope; the round-trip is acceptable because MCP
    config sections are purely data.
    """
    if admin_toml and admin_toml.strip():
        try:
            base: dict = tomllib.loads(admin_toml)
        except tomllib.TOMLDecodeError:
            return admin_toml
    else:
        base = {}

    existing = base.get("mcp_servers")
    if not isinstance(existing, dict):
        existing = {}
    merged = dict(existing)
    for overlay in overlays:
        merged.setdefault(overlay.name, overlay.config)
    base["mcp_servers"] = merged

    return tomli_w.dumps(base)


# ── Top-level dispatcher ─────────────────────────────────────────


def merge_for_engine(
    *,
    engine: str,
    admin_content: str | None,
    overlays: list[RenderedInstance],
) -> str:
    """Return the merged manifest body for the engine's settings file.

    Callers that have already ensured ``engine`` is supported can
    expect a string back. Unsupported engines raise ``ValueError``
    because feeding an empty string through would silently drop the
    overlays.
    """
    if engine in ("claude-code", "gemini-cli"):
        return merge_json_settings(admin_content, overlays)
    if engine == "codex":
        return merge_codex_config(admin_content, overlays)
    raise ValueError(f"Unsupported MCP engine: {engine}")
