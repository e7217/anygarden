"""One daemon per agent directory, and per-daemon state paths (#848)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anygarden.machine import config
from anygarden.machine.agent_root_lock import AgentRootInUseError, AgentRootLock
from anygarden.machine.config import MachinePaths


@pytest.mark.req("NODE-11")
def test_second_lock_on_same_root_is_refused(tmp_path: Path) -> None:
    first = AgentRootLock(tmp_path / "agents")
    first.acquire()
    second = AgentRootLock(tmp_path / "agents")
    with pytest.raises(AgentRootInUseError) as exc:
        second.acquire()
    assert "--data-dir" in str(exc.value)
    assert not second.held
    first.release()
    second.acquire()
    assert second.held
    second.release()


def test_locks_on_different_roots_are_independent(tmp_path: Path) -> None:
    node = AgentRootLock(tmp_path / "node" / "agents")
    link = AgentRootLock(tmp_path / "remote-machines" / "m1" / "agents")
    node.acquire()
    link.acquire()
    assert node.held and link.held
    node.release()
    link.release()


def test_acquire_is_idempotent_and_creates_private_root(tmp_path: Path) -> None:
    lock = AgentRootLock(tmp_path / "agents")
    lock.acquire()
    lock.acquire()
    assert (tmp_path / "agents" / ".daemon.lock").exists()
    assert (tmp_path / "agents").stat().st_mode & 0o077 == 0
    lock.release()
    lock.release()


def test_symlinked_root_is_refused(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "agents").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError):
        AgentRootLock(tmp_path / "agents").acquire()


def test_default_paths_keep_the_historical_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "ANYGARDEN_DIR", tmp_path / ".anygarden")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".anygarden" / "machine.toml")
    monkeypatch.setattr(config, "TOKEN_PATH", tmp_path / ".anygarden" / "machine.token")
    paths = MachinePaths.for_data_dir(None)
    assert paths.config == tmp_path / ".anygarden" / "machine.toml"
    assert paths.token == tmp_path / ".anygarden" / "machine.token"
    assert paths.agents_root == tmp_path / ".anygarden" / "agents"
    # The daemon keeps its own historical defaults for these.
    assert paths.workspace_registry is None
    assert paths.signing_key is None
    assert paths.data_dir is None


def test_data_dir_holds_every_daemon_owned_file(tmp_path: Path) -> None:
    root = tmp_path / "remote-machines" / "m1"
    paths = MachinePaths.for_data_dir(root)
    assert paths.data_dir == root
    assert paths.config == root / "machine.toml"
    assert paths.token == root / "machine.token"
    assert paths.agents_root == root / "agents"
    assert paths.workspace_registry == root / "workspaces.json"
    assert paths.signing_key == root / "workspace-signing.key"


def test_symlinked_data_dir_is_refused(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError):
        MachinePaths.for_data_dir(tmp_path / "link")


def test_save_connection_writes_into_the_data_dir(tmp_path: Path) -> None:
    paths = MachinePaths.for_data_dir(tmp_path / "d")
    cfg = config.MachineConfig(machine_id="m1", name="Y", server_url="ws://x/ws/machines/m1")
    config.save_connection(cfg, "mch_" + "a" * 20, paths)
    assert config.MachineConfig.load(paths.config).machine_id == "m1"
    assert config.load_token(paths.token) == "mch_" + "a" * 20
