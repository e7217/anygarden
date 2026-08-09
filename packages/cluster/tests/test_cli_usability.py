"""Contract tests tying the public CLI docs to the real Click commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from anygarden import cli
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _clear_server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ANYGARDEN_HOST",
        "ANYGARDEN_PORT",
        "ANYGARDEN_DB_URL",
        "ANYGARDEN_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_explicit_config_is_loaded_and_cli_values_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "server.env"
    config_path.write_text(
        "ANYGARDEN_HOST=10.0.0.8\n"
        "ANYGARDEN_PORT=8111\n"
        "ANYGARDEN_DB_URL=sqlite+aiosqlite:///from-file.db\n"
        "ANYGARDEN_LOG_LEVEL=DEBUG\n"
    )
    monkeypatch.setenv("ANYGARDEN_PORT", "8222")

    from_file = cli._load_server_settings(None, None, None, None, str(config_path))
    assert from_file.host == "10.0.0.8"
    assert from_file.port == 8222  # process environment wins over .env
    assert from_file.db_url.endswith("from-file.db")
    assert from_file.log_level == "DEBUG"

    overridden = cli._load_server_settings(
        "0.0.0.0",
        8333,
        "sqlite+aiosqlite:///from-cli.db",
        "WARNING",
        str(config_path),
    )
    assert overridden.host == "0.0.0.0"
    assert overridden.port == 8333
    assert overridden.db_url.endswith("from-cli.db")
    assert overridden.log_level == "WARNING"


def test_default_init_config_is_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".anygarden"
    config_dir.mkdir()
    (config_dir / "config.env").write_text("ANYGARDEN_PORT=8444\n")

    config = cli._load_server_settings(None, None, None, None, None)
    assert config.port == 8444


@pytest.mark.parametrize(
    ("args", "expected_options"),
    [
        (["--help"], ["server", "machine", "agent", "client"]),
        (["server", "--help"], ["--host", "--port", "--config"]),
        (["machine", "run", "--help"], ["--server", "--config", "--machine-id"]),
        (["agent", "--help"], ["--engine", "--name", "--server", "--room"]),
        (["client", "--help"], ["--server", "--user", "--room"]),
    ],
)
def test_documented_help_commands_smoke(
    args: list[str], expected_options: list[str]
) -> None:
    result = CliRunner().invoke(cli.dispatch, args)
    assert result.exit_code == 0, result.output
    for option in expected_options:
        assert option in result.output


def test_readmes_do_not_advertise_removed_or_incomplete_commands() -> None:
    cluster_dir = Path(__file__).parents[1]
    cluster_readme = (cluster_dir / "README.md").read_text()
    agent_readme = (cluster_dir.parent / "agent" / "README.md").read_text()

    assert "--engine openai" not in agent_readme
    assert "dragent[openai]" not in agent_readme
    assert "dragent[all-engines]" not in agent_readme
    assert "--daemon" not in cluster_readme
    assert "--name demo-agent" in cluster_readme
    assert "--server ws://localhost:8000" in cluster_readme
