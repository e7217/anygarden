"""Tests for CLI entry points (anygarden-agent, anygarden-client)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from anygarden_agent.cli import agent_main, client_main
from anygarden_agent.integrations import ENGINES, get_adapter
from anygarden_agent.profile.loader import load_profile
from click.testing import CliRunner


class TestAgentCLI:
    def test_agent_help(self) -> None:
        """anygarden-agent --help exits cleanly and shows engine choices."""
        runner = CliRunner()
        result = runner.invoke(agent_main, ["--help"])
        assert result.exit_code == 0
        assert "--engine" in result.output
        # Every supported engine should appear in the help text.
        for engine_name in ENGINES:
            assert engine_name in result.output

    def test_readme_engine_examples_use_supported_choices(self) -> None:
        """README commands must stay aligned with Click's engine choices."""
        readme = (Path(__file__).parents[1] / "README.md").read_text()
        documented = set(re.findall(r"--engine\s+([a-z0-9-]+)", readme))
        assert documented
        assert documented <= set(ENGINES)


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


@pytest.mark.parametrize(
    "retired", ["claude-code", "gemini-cli", "openhands", "claude_code", "gemini_cli"]
)
def test_retired_cli_and_profile_refuse_before_connection(
    retired, tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    runner = CliRunner()
    run = AsyncMock()
    monkeypatch.setattr("anygarden_agent.cli._run_agent", run)
    result = runner.invoke(
        agent_main, ["--engine", retired, "--server", "ws://localhost:1"]
    )
    assert result.exit_code != 0 and "preserved" in result.output
    profile = tmp_path / "legacy.yaml"
    content = yaml.safe_dump(
        dict(
            name="legacy", engine=retired, server_url="ws://localhost:1", rooms=["room"]
        )
    )
    profile.write_text(content)
    result = runner.invoke(
        agent_main, ["--profile", str(profile.with_suffix("")), "--server", "ws://localhost:1"]
    )
    assert result.exit_code != 0 and "preserved" in result.output
    assert profile.read_text() == content
    run.assert_not_called()
    with pytest.raises(ValueError, match="preserved"):
        get_adapter(retired)
