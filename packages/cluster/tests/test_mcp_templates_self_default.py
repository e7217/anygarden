"""Tests for the doorae self-MCP default entry builder (#277)."""

from __future__ import annotations

from doorae.mcp_templates.merge import (
    DOORAE_BUILTIN_NAME,
    RenderedInstance,
    doorae_default_entry,
    merge_for_engine,
    settings_path_for_engine,
)


class TestDoorAEDefaultEntry:
    def test_claude_code_uses_streamable_http_with_bearer_header(self):
        entry = doorae_default_entry(
            engine="claude-code",
            cluster_url="http://localhost:8001",
            agent_token="tok-abc",
        )
        assert entry is not None
        assert entry.name == DOORAE_BUILTIN_NAME == "doorae"
        assert entry.config == {
            "type": "http",
            "url": "http://localhost:8001/mcp/rpc",
            "headers": {"Authorization": "Bearer tok-abc"},
        }

    def test_gemini_cli_uses_same_shape_as_claude(self):
        entry = doorae_default_entry(
            engine="gemini-cli",
            cluster_url="https://chat.example.com",
            agent_token="tok-xyz",
        )
        assert entry is not None
        assert entry.config == {
            "type": "http",
            "url": "https://chat.example.com/mcp/rpc",
            "headers": {"Authorization": "Bearer tok-xyz"},
        }

    def test_codex_uses_bearer_token_env_var_form(self):
        # Codex CLI 0.124+ understands ``url + bearer_token_env_var``;
        # disk never sees the raw token. The matching env var is
        # injected by the machine spawner at process start time.
        entry = doorae_default_entry(
            engine="codex",
            cluster_url="http://127.0.0.1:8001",
            agent_token="tok-abc",  # not stored in the rendered config
        )
        assert entry is not None
        assert entry.config == {
            "url": "http://127.0.0.1:8001/mcp/rpc",
            "bearer_token_env_var": "DOORAE_AGENT_TOKEN",
        }
        # Defensive: token must NOT leak into the codex config —
        # that's the whole point of the env-var indirection.
        assert "tok-abc" not in str(entry.config)

    def test_unsupported_engines_return_none(self):
        # Engines without MCP support (or without a settings file
        # path in ``settings_path_for_engine``) get a None back so
        # the spawn pipeline can skip without a guard at every
        # call site.
        for engine in ("openai", "anthropic", "echo", "unknown-engine"):
            assert doorae_default_entry(
                engine=engine,
                cluster_url="http://x",
                agent_token="t",
            ) is None
            # And those engines also have no settings file —
            # consistency check, not strictly required by this PR.
            assert settings_path_for_engine(engine) is None

    def test_default_entry_returns_rendered_instance(self):
        # Type contract — callers (lifecycle.py) feed this directly
        # into the existing ``overlays`` list, so the dataclass shape
        # must match ``render_instance``'s output.
        entry = doorae_default_entry(
            engine="claude-code",
            cluster_url="http://x",
            agent_token="t",
        )
        assert isinstance(entry, RenderedInstance)


class TestMergeWithDefault:
    """The default entry must flow through the existing merge
    helpers without any signature change — that's the load-bearing
    invariant of the whole approach (plan §3.2 결정 1)."""

    def test_claude_code_merge_includes_doorae(self):
        default = doorae_default_entry(
            engine="claude-code",
            cluster_url="http://localhost:8001",
            agent_token="tok",
        )
        assert default is not None
        merged = merge_for_engine(
            engine="claude-code",
            admin_content=None,
            overlays=[default],
        )
        import json as _json

            # Helper: parse and assert
        data = _json.loads(merged)
        assert "doorae" in data["mcpServers"]
        assert data["mcpServers"]["doorae"]["type"] == "http"
        assert "Bearer tok" in (
            data["mcpServers"]["doorae"]["headers"]["Authorization"]
        )

    def test_codex_merge_includes_doorae(self):
        secret_token = "PLAINTEXT_TOKEN_THAT_MUST_NOT_LEAK"
        default = doorae_default_entry(
            engine="codex",
            cluster_url="http://localhost:8001",
            agent_token=secret_token,
        )
        assert default is not None
        merged = merge_for_engine(
            engine="codex",
            admin_content=None,
            overlays=[default],
        )
        import tomllib as _tomllib

        data = _tomllib.loads(merged)
        assert "doorae" in data["mcp_servers"]
        assert (
            data["mcp_servers"]["doorae"]["bearer_token_env_var"]
            == "DOORAE_AGENT_TOKEN"
        )
        # Crucial — the codex form's whole point is to keep the
        # plaintext token off disk. Verify the merge did not somehow
        # inject it into the rendered manifest.
        assert secret_token not in merged

    def test_admin_overrides_doorae_when_same_name(self):
        """Plan §3.2 결정 1 escape hatch: if an admin manually
        registers an external server named ``doorae`` it wins
        on key collision (existing ``setdefault`` semantics)."""
        default = doorae_default_entry(
            engine="claude-code",
            cluster_url="http://localhost:8001",
            agent_token="tok",
        )
        assert default is not None
        admin_content = (
            '{"mcpServers": {"doorae": '
            '{"command": "npx", "args": ["-y", "custom"], "env": {}}}}'
        )
        merged = merge_for_engine(
            engine="claude-code",
            admin_content=admin_content,
            overlays=[default],
        )
        import json as _json

        data = _json.loads(merged)
        # Admin's stdio config wins over the builtin streamable HTTP.
        assert data["mcpServers"]["doorae"]["command"] == "npx"
        assert "type" not in data["mcpServers"]["doorae"]
