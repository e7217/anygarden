"""Persist web-issued machine identities without registering duplicate machines."""

from __future__ import annotations

import stat
import subprocess
from unittest.mock import AsyncMock

import pytest
from anygarden_machine import cli, config, workspace_registry
from click.testing import CliRunner

TOKEN = "mch_" + "a" * 43


@pytest.fixture()
def saved_files(tmp_path, monkeypatch):
    folder = tmp_path / "machine-settings"
    monkeypatch.setattr(config, "CONFIG_PATH", folder / "machine.toml")
    monkeypatch.setattr(config, "TOKEN_PATH", folder / "machine.token")
    return folder


def test_connect_saves_private_files_and_run_reads_them(saved_files, monkeypatch):
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "connect",
            "--server",
            "https://garden.test/base",
            "--machine-id",
            "machine-1",
        ],
        input=TOKEN + "\n",
    )
    assert result.exit_code == 0, result.output
    assert TOKEN not in result.output
    saved = config.MachineConfig.load()
    assert saved.machine_id == "machine-1"
    assert saved.server_url == "wss://garden.test/base/ws/machines/machine-1"
    assert config.load_token() == TOKEN
    assert TOKEN not in config.CONFIG_PATH.read_text()
    assert stat.S_IMODE(config.TOKEN_PATH.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.CONFIG_PATH.stat().st_mode) == 0o600
    captures = []

    def daemon(**kwargs):
        captures.append(kwargs)
        return type("Daemon", (), {"run": AsyncMock()})()

    monkeypatch.setattr(cli, "MachineDaemon", daemon)
    result = runner.invoke(cli.main, ["run"])
    assert result.exit_code == 0, result.output
    assert captures[0]["machine_id"] == "machine-1"
    assert captures[0]["machine_token"] == TOKEN
    assert captures[0]["server_url"] == saved.server_url


def test_connect_does_not_silently_replace_another_saved_machine(saved_files):
    runner = CliRunner()
    assert (
        runner.invoke(
            cli.main,
            ["connect", "--server", "http://garden.test", "--machine-id", "first"],
            input=TOKEN + "\n",
        ).exit_code
        == 0
    )
    rejected = runner.invoke(
        cli.main,
        ["connect", "--server", "http://garden.test", "--machine-id", "second"],
        input=TOKEN + "\n",
    )
    assert rejected.exit_code == 1
    assert "--replace" in rejected.output
    assert config.MachineConfig.load().machine_id == "first"
    accepted = runner.invoke(
        cli.main,
        [
            "connect",
            "--server",
            "http://garden.test",
            "--machine-id",
            "second",
            "--replace",
        ],
        input=TOKEN + "\n",
    )
    assert accepted.exit_code == 0
    assert config.MachineConfig.load().machine_id == "second"


@pytest.mark.parametrize(
    "server",
    [
        "file:///tmp/server",
        "http://user:password@host",
        "https://host/?x=1",
        "http://[invalid",
    ],
)
def test_connect_rejects_invalid_server_before_persisting(saved_files, server):
    result = CliRunner().invoke(
        cli.main,
        ["connect", "--server", server, "--machine-id", "worker"],
        input=TOKEN + "\n",
    )
    assert result.exit_code != 0
    assert not config.CONFIG_PATH.exists()
    assert not config.TOKEN_PATH.exists()


def test_invalid_token_preserves_the_existing_connection(saved_files):
    original = config.MachineConfig(
        machine_id="worker", name="worker", server_url="ws://host/ws/machines/worker"
    )
    config.save_connection(original, TOKEN)
    result = CliRunner().invoke(
        cli.main,
        ["connect", "--server", "http://host", "--machine-id", "worker"],
        input="incorrect\n",
    )
    assert result.exit_code == 1
    assert config.load_token() == TOKEN
    assert config.MachineConfig.load() == original


def test_token_is_rolled_back_if_config_save_fails(saved_files, monkeypatch):
    config.save_token(TOKEN)
    monkeypatch.setattr(
        config.MachineConfig,
        "save",
        lambda *_: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        config.save_connection(
            config.MachineConfig(machine_id="new"), "mch_replacement"
        )
    assert config.load_token() == TOKEN


def test_token_save_replaces_symlink_without_writing_its_target(saved_files, tmp_path):
    original = tmp_path / "unrelated-file"
    original.write_text("keep me")
    saved_files.mkdir()
    config.TOKEN_PATH.symlink_to(original)
    config.save_token(TOKEN)
    assert original.read_text() == "keep me"
    assert not config.TOKEN_PATH.is_symlink()
    assert config.load_token() == TOKEN


def test_workspace_cli_uses_the_integrated_nodes_registry(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "file.txt").write_text("test")
    subprocess.run(
        ["git", "-C", str(repo), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "test",
        ],
        check=True,
        capture_output=True,
    )
    node_dir = tmp_path / "node"
    node_dir.mkdir()
    remote_registry = tmp_path / "remote" / "workspaces.json"
    monkeypatch.setattr(workspace_registry, "DEFAULT_REGISTRY_PATH", remote_registry)
    prefix = ["workspace", "--node-data-dir", str(node_dir)]
    registered = CliRunner().invoke(
        cli.main,
        [*prefix, "register", str(repo), "--label", "Project", "--allow", "src"],
    )
    assert registered.exit_code == 0, registered.output
    workspace_id = registered.output.strip()
    registry = workspace_registry.WorkspaceRegistry(
        node_dir / "workspace-registry.json"
    )
    assert registry.list_descriptors()[0]["workspace_id"] == workspace_id
    assert not remote_registry.exists()
    consent = CliRunner().invoke(
        cli.main,
        [
            *prefix,
            "consent",
            workspace_id,
            "--agent-id",
            "agent-1",
            "--room-id",
            "room-1",
            "--mode",
            "read",
        ],
    )
    assert consent.exit_code == 0, consent.output
    assert consent.output.strip().startswith("wcp_")
    assert not remote_registry.exists()


@pytest.mark.parametrize(
    "name", ["Office\rWorker", "Office\b\f\x00\x1f\x7f", "사무실 🤖"]
)
def test_connect_name_round_trips_as_valid_toml(saved_files, name):
    result = CliRunner().invoke(
        cli.main,
        [
            "connect",
            "--server",
            "https://garden.example.com",
            "--machine-id",
            "machine-1",
            "--name",
            name,
        ],
        input="mch_testtoken123\n",
    )
    assert result.exit_code == 0, result.output
    assert config.MachineConfig.load().name == name
