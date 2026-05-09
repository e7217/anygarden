"""Builtin MCP server templates seeded at cluster startup (#124).

Each entry here produces one row in ``mcp_server_templates`` with
``source="builtin"``. The seed step is idempotent: on startup, the
service upserts by ``name`` so updating this module and redeploying
propagates new config to the DB without duplicating rows.

Design notes:

- All four supported engines (claude-code, codex, gemini-cli,
  openhands [#355]) accept the same **structural** description
  (``command`` + ``args`` + ``env`` map), so we store a single dict
  per engine rather than inventing an abstract schema that has to
  translate. ``merge.py`` knows how to render the dict into
  ``mcpServers.<name>`` JSON for claude-code / gemini-cli /
  openhands and ``[mcp_servers.<name>]`` TOML for codex.
- env placeholders use ``${VAR}`` style. At render time
  :func:`doorae.mcp_templates.merge.substitute_env_placeholders`
  interpolates the decrypted credential values into the final
  config. Admins setting up a custom template follow the same
  convention so builtin and custom render identically.
- We deliberately *do not* pin npm package versions here. The CLI
  engines run MCP servers via ``npx -y <pkg>`` which resolves the
  latest compatible version at spawn time; pinning here would force
  cluster redeploys for every upstream MCP server release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class BuiltinTemplateSpec:
    """Immutable description of one builtin template.

    Kept as a dataclass (not a Pydantic model) because the data is
    static compile-time constant — Pydantic's runtime validation
    overhead buys nothing here, and a frozen dataclass prevents
    accidental mutation of the shared BUILTIN_TEMPLATES list.
    """

    name: str
    display_name: str
    description: str
    icon: Optional[str]
    config_per_engine: dict[str, dict]
    required_env_vars: list[str]
    # ``supported_engines`` is redundant with ``config_per_engine.keys()``
    # but we spell it out so the DB row has a normalised column the
    # API can filter on without parsing the config blob.
    supported_engines: list[str] = field(default_factory=list)


def _stdio_config(command: str, args: list[str], env: dict[str, str]) -> dict:
    """Return the structural config dict shared by all three engines."""
    return {"command": command, "args": args, "env": env}


def _all_engines_stdio(
    command: str,
    args: list[str],
    env: dict[str, str],
) -> dict[str, dict]:
    """Render the same stdio config for every MCP-supporting engine.

    Kept in one place because every builtin below uses the identical
    config across engines; if a future template needs engine-specific
    divergence it can author its own ``config_per_engine`` literal
    instead.

    Issue #355 Phase 1 — extended to include openhands. The OpenHands
    SDK accepts the same ``{command, args, env}`` stdio shape via
    FastMCP, and the merge layer routes openhands through the same
    JSON path as claude-code, so adding the fourth key has zero
    cost beyond the literal entry below.
    """
    cfg = _stdio_config(command, args, env)
    return {
        "claude-code": cfg,
        "codex": cfg,
        "gemini-cli": cfg,
        "openhands": cfg,
    }


# Back-compat alias: pre-#355 callers (none in tree at the time of
# this commit, but external integrations may exist) imported the
# three-engine helper by name. Removing the symbol would silently
# break them at import; aliasing keeps the name resolvable while the
# canonical helper grew a fourth engine.
_three_engine_stdio = _all_engines_stdio


BUILTIN_TEMPLATES: list[BuiltinTemplateSpec] = [
    BuiltinTemplateSpec(
        name="github",
        display_name="GitHub",
        description=(
            "Access GitHub repos, issues, and pull requests via the "
            "official MCP server."
        ),
        icon="github",
        config_per_engine=_all_engines_stdio(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"},
        ),
        required_env_vars=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        supported_engines=["claude-code", "codex", "gemini-cli", "openhands"],
    ),
    BuiltinTemplateSpec(
        name="slack",
        display_name="Slack",
        description=(
            "Read and post in Slack channels via the official MCP server."
        ),
        icon="slack",
        config_per_engine=_all_engines_stdio(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-slack"],
            env={
                "SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}",
                "SLACK_TEAM_ID": "${SLACK_TEAM_ID}",
            },
        ),
        required_env_vars=["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        supported_engines=["claude-code", "codex", "gemini-cli", "openhands"],
    ),
    BuiltinTemplateSpec(
        name="notion",
        display_name="Notion",
        description="Search and read Notion pages via the MCP server.",
        icon="notion",
        config_per_engine=_all_engines_stdio(
            command="npx",
            args=["-y", "@notionhq/notion-mcp-server"],
            env={"NOTION_API_KEY": "${NOTION_API_KEY}"},
        ),
        required_env_vars=["NOTION_API_KEY"],
        supported_engines=["claude-code", "codex", "gemini-cli", "openhands"],
    ),
    BuiltinTemplateSpec(
        name="linear",
        display_name="Linear",
        description="Query and update Linear issues via the MCP server.",
        icon="linear",
        config_per_engine=_all_engines_stdio(
            command="npx",
            args=["-y", "@linear/mcp-server"],
            env={"LINEAR_API_KEY": "${LINEAR_API_KEY}"},
        ),
        required_env_vars=["LINEAR_API_KEY"],
        supported_engines=["claude-code", "codex", "gemini-cli", "openhands"],
    ),
    BuiltinTemplateSpec(
        name="filesystem",
        display_name="Filesystem",
        description=(
            "Read / write files in a bounded directory. No credentials — "
            "the allowed path is set per instance via the "
            "MCP_FS_ALLOWED_PATH env value."
        ),
        icon="folder",
        config_per_engine=_all_engines_stdio(
            command="npx",
            args=[
                "-y",
                "@modelcontextprotocol/server-filesystem",
                "${MCP_FS_ALLOWED_PATH}",
            ],
            env={},
        ),
        required_env_vars=["MCP_FS_ALLOWED_PATH"],
        supported_engines=["claude-code", "codex", "gemini-cli", "openhands"],
    ),
]
