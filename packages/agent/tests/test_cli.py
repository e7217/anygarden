"""Tests for CLI entry points (anygarden-agent, anygarden-client)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from anygarden_agent.cli import agent_main, client_main
from anygarden_agent.integrations import ENGINES, get_adapter
from anygarden_agent.profile.loader import load_profile
from anygarden_agent.profile.schema import AgentProfile


class TestAgentCLI:
    def test_agent_help(self) -> None:
        """anygarden-agent --help exits cleanly and shows engine choices."""
        runner = CliRunner()
        result = runner.invoke(agent_main, ["--help"])
        assert result.exit_code == 0
        assert "--engine" in result.output
        # All 6 engines should appear in the help text
        for engine_name in ENGINES:
            assert engine_name in result.output


class TestClientCLI:
    def test_client_help(self) -> None:
        """anygarden-client --help exits cleanly."""
        runner = CliRunner()
        result = runner.invoke(client_main, ["--help"])
        assert result.exit_code == 0
        assert "--server" in result.output
        assert "--user" in result.output


class TestProfileLoading:
    def test_load_example_profile(self, tmp_path: Path) -> None:
        """Load a YAML profile and validate it against AgentProfile schema."""
        profile_data = {
            "name": "TestBot",
            "engine": "claude-code",
            "model": "claude-sonnet-4-6",
            "system_prompt": "You are a test bot.",
            "rooms": ["main"],
            "mcp_servers": [],
        }
        profile_file = tmp_path / "testbot.yaml"
        profile_file.write_text(yaml.dump(profile_data))

        profile = load_profile("testbot", agents_dir=tmp_path)
        assert profile.name == "TestBot"
        assert profile.engine == "claude-code"
        assert profile.rooms == ["main"]


class TestEngineSelection:
    def test_get_adapter_all_engines(self) -> None:
        """get_adapter returns an adapter instance for each known engine."""
        for engine_name in ENGINES:
            adapter = get_adapter(engine_name)
            assert adapter is not None

    def test_get_adapter_unknown_engine(self) -> None:
        """get_adapter raises ValueError for unknown engine names."""
        with pytest.raises(ValueError, match="Unknown engine"):
            get_adapter("nonexistent-engine")
