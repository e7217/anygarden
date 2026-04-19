"""Unit tests for the engine-specific manifest merging (#124)."""

from __future__ import annotations

import json
import tomllib

from doorae.mcp_templates.merge import (
    RenderedInstance,
    merge_codex_config,
    merge_for_engine,
    merge_json_settings,
    render_instance,
    settings_path_for_engine,
    substitute_env_placeholders,
)


class TestPlaceholderSubstitution:
    def test_single_var_in_string(self):
        out = substitute_env_placeholders("${TOK}", {"TOK": "secret"})
        assert out == "secret"

    def test_var_inside_path(self):
        out = substitute_env_placeholders(
            "/home/${USER}/data", {"USER": "alice"},
        )
        assert out == "/home/alice/data"

    def test_unresolved_stays_as_is(self):
        # Keeps rendering robust — admins can rely on os.environ
        # when they don't want the cluster to store a value.
        out = substitute_env_placeholders("${UNSET}", {"OTHER": "v"})
        assert out == "${UNSET}"

    def test_recurses_into_list_and_dict(self):
        out = substitute_env_placeholders(
            {"env": {"X": "${X}"}, "args": ["--token=${TOK}"]},
            {"X": "x", "TOK": "t"},
        )
        assert out == {"env": {"X": "x"}, "args": ["--token=t"]}


class TestRenderInstance:
    def test_picks_engine_block_and_substitutes(self):
        result = render_instance(
            name="github",
            config_per_engine={
                "claude-code": {
                    "command": "npx",
                    "args": ["-y", "x"],
                    "env": {"T": "${T}"},
                },
                "codex": {"command": "npx"},
            },
            env_values={"T": "secret"},
            engine="claude-code",
        )
        assert result is not None
        assert result.name == "github"
        assert result.config == {
            "command": "npx",
            "args": ["-y", "x"],
            "env": {"T": "secret"},
        }

    def test_unsupported_engine_returns_none(self):
        # Attach-time validation should prevent this, but render is
        # defensive against a stale DB state.
        result = render_instance(
            name="github",
            config_per_engine={"claude-code": {"command": "x"}},
            env_values={},
            engine="codex",
        )
        assert result is None


class TestJsonMerge:
    def _overlay(self, name, **kwargs):
        return RenderedInstance(name=name, config=kwargs)

    def test_seeds_from_empty_admin_content(self):
        out = merge_json_settings(None, [self._overlay("github", command="npx")])
        data = json.loads(out)
        assert data["mcpServers"]["github"] == {"command": "npx"}

    def test_preserves_admin_top_level_keys(self):
        admin = json.dumps({
            "permissions": {"allow": ["Read", "Write"]},
            "mcpServers": {"existing": {"command": "y"}},
        })
        out = merge_json_settings(admin, [self._overlay("new", command="x")])
        data = json.loads(out)
        assert data["permissions"] == {"allow": ["Read", "Write"]}
        assert data["mcpServers"]["existing"] == {"command": "y"}
        assert data["mcpServers"]["new"] == {"command": "x"}

    def test_admin_wins_on_name_collision(self):
        """If the admin already defines mcpServers.github, don't
        overwrite — plan §3 sets the precedence so admins always
        have the final word on a given server name."""
        admin = json.dumps({
            "mcpServers": {"github": {"command": "admin-command"}},
        })
        out = merge_json_settings(
            admin, [self._overlay("github", command="overlay-command")],
        )
        data = json.loads(out)
        assert data["mcpServers"]["github"] == {"command": "admin-command"}

    def test_malformed_admin_json_is_left_untouched(self):
        # A broken settings.json shouldn't have our overlay silently
        # clobber it — the engine will surface the parse error on
        # spawn, which is where the admin can debug.
        broken = "{ not: valid }"
        out = merge_json_settings(broken, [self._overlay("github")])
        assert out == broken


class TestCodexMerge:
    def _overlay(self, name, **kwargs):
        return RenderedInstance(name=name, config=kwargs)

    def test_seeds_from_empty_admin_content(self):
        out = merge_codex_config(None, [self._overlay(
            "github", command="npx", args=["-y", "x"],
            env={"T": "tok"},
        )])
        parsed = tomllib.loads(out)
        assert parsed["mcp_servers"]["github"]["command"] == "npx"
        assert parsed["mcp_servers"]["github"]["args"] == ["-y", "x"]
        assert parsed["mcp_servers"]["github"]["env"]["T"] == "tok"

    def test_preserves_admin_sections(self):
        admin_toml = """
[general]
model = "gpt-5.4"

[mcp_servers.existing]
command = "original"
""".strip()
        out = merge_codex_config(admin_toml, [self._overlay("new", command="x")])
        parsed = tomllib.loads(out)
        assert parsed["general"]["model"] == "gpt-5.4"
        assert parsed["mcp_servers"]["existing"]["command"] == "original"
        assert parsed["mcp_servers"]["new"]["command"] == "x"

    def test_admin_wins_on_name_collision(self):
        admin_toml = """
[mcp_servers.github]
command = "admin"
""".strip()
        out = merge_codex_config(admin_toml, [self._overlay("github", command="overlay")])
        parsed = tomllib.loads(out)
        assert parsed["mcp_servers"]["github"]["command"] == "admin"


class TestDispatcher:
    def test_claude_code_uses_json(self):
        out = merge_for_engine(
            engine="claude-code",
            admin_content=None,
            overlays=[RenderedInstance(name="x", config={"command": "y"})],
        )
        data = json.loads(out)
        assert data["mcpServers"]["x"]["command"] == "y"

    def test_gemini_uses_json(self):
        out = merge_for_engine(
            engine="gemini-cli",
            admin_content=None,
            overlays=[RenderedInstance(name="x", config={"command": "y"})],
        )
        json.loads(out)  # must be parseable

    def test_codex_uses_toml(self):
        out = merge_for_engine(
            engine="codex",
            admin_content=None,
            overlays=[RenderedInstance(name="x", config={"command": "y"})],
        )
        parsed = tomllib.loads(out)
        assert "mcp_servers" in parsed

    def test_unsupported_engine_raises(self):
        import pytest
        with pytest.raises(ValueError):
            merge_for_engine(engine="openai", admin_content=None, overlays=[])


class TestSettingsPath:
    def test_maps_engines_to_paths(self):
        # Issue #142 — Claude Code 2.x reads project-local MCP config
        # from .mcp.json at the workspace root, not from the
        # .claude/settings.json "mcpServers" section (which is
        # silently ignored). Pin the file name.
        assert settings_path_for_engine("claude-code") == ".mcp.json"
        assert settings_path_for_engine("codex") == ".codex/config.toml"
        assert settings_path_for_engine("gemini-cli") == ".gemini/settings.json"

    def test_non_mcp_engines_return_none(self):
        assert settings_path_for_engine("openai") is None
        assert settings_path_for_engine("anthropic") is None
        assert settings_path_for_engine("echo") is None
