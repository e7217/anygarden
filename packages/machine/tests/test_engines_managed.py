"""#688 — AnyGarden-managed, version-pinned Pi install."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from anygarden_machine.detector import detect_engines
from anygarden_machine.engines import managed
from anygarden_machine.engines.channels import NpmManagedPrefix
from anygarden_machine.engines.registry import get_lifecycle
from anygarden_machine.engines.updater import run_engine_update


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = tmp_path / "engines"
    monkeypatch.setenv(managed.ROOT_ENV, str(path))
    monkeypatch.delenv(managed.PATH_OPT_IN_ENV, raising=False)
    monkeypatch.delenv(managed.PROVISION_ENV, raising=False)
    return path


def fake_pi(path: Path, version: str = managed.PI_PINNED_VERSION) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho {version}\n")
    path.chmod(0o755)
    return path


def seed_managed(root: Path, version: str = managed.PI_PINNED_VERSION) -> Path:
    return fake_pi(root / "pi-cli" / version / "node_modules" / ".bin" / "pi", version)


def test_prefix_is_versioned_under_managed_root(root):
    assert managed.managed_prefix() == root / "pi-cli" / managed.PI_PINNED_VERSION
    assert managed.managed_pi_executable() is None
    exe = seed_managed(root)
    assert managed.managed_pi_executable() == exe


def test_non_executable_file_is_not_an_install(root):
    exe = seed_managed(root)
    exe.chmod(0o644)
    assert managed.managed_pi_executable() is None


def test_pinned_version_matches_agent_adapter():
    pi = pytest.importorskip("anygarden_agent.runtime.execution.pi")
    assert managed.PI_PINNED_VERSION == pi.ENGINE_VERSION


def test_path_opt_in_is_explicit(root, monkeypatch):
    assert managed.pi_path_opt_in() is False
    monkeypatch.setenv(managed.PATH_OPT_IN_ENV, "true")
    assert managed.pi_path_opt_in() is False
    monkeypatch.setenv(managed.PATH_OPT_IN_ENV, "1")
    assert managed.pi_path_opt_in() is True


# ── Channel / updater ───────────────────────────────────────────────


async def test_managed_channel_is_pinned_and_offline(root):
    channel = NpmManagedPrefix(managed.PI_PINNED_VERSION, managed.managed_prefix)
    assert channel.kind == "npm-managed"
    assert await channel.latest_version("@earendil-works/pi-coding-agent") == "0.85.1"
    assert channel.normalize("pi 0.85.1") == "0.85.1"


def test_pi_lifecycle_uses_managed_channel(root):
    lc = get_lifecycle("pi-cli")
    assert lc.channel.kind == "npm-managed"
    assert lc.detect.mode == "managed"


def test_update_engine_installs_pinned_version_into_prefix(root):
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = run_engine_update("pi-cli", runner=runner)
    assert result.ok, result.error
    prefix = root / "pi-cli" / managed.PI_PINNED_VERSION
    assert calls == [
        [
            "npm",
            "install",
            "--prefix",
            str(prefix),
            "--no-audit",
            "--no-fund",
            "@earendil-works/pi-coding-agent@0.85.1",
        ]
    ]
    assert "-g" not in calls[0]
    assert prefix.is_dir()


# ── Detection ───────────────────────────────────────────────────────


async def test_detects_managed_install_first(root, tmp_path, monkeypatch):
    fake_pi(tmp_path / "bin" / "pi", "0.87.1")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    exe = seed_managed(root)
    pi = next(e for e in (await detect_engines()).engines if e.engine == "pi-cli")
    assert (pi.version, pi.path) == ("0.85.1", str(exe))


async def test_global_pi_is_ignored_without_opt_in(root, tmp_path, monkeypatch):
    fake_pi(tmp_path / "bin" / "pi", "0.87.1")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    engines = [e.engine for e in (await detect_engines()).engines]
    assert "pi-cli" not in engines


async def test_global_pi_detected_with_opt_in(root, tmp_path, monkeypatch):
    binary = fake_pi(tmp_path / "bin" / "pi", "0.87.1")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv(managed.PATH_OPT_IN_ENV, "1")
    pi = next(e for e in (await detect_engines()).engines if e.engine == "pi-cli")
    assert (pi.version, pi.path) == ("0.87.1", str(binary))


# ── Provisioning ────────────────────────────────────────────────────


def installing_runner(root, calls):
    def runner(cmd, **kwargs):
        calls.append(cmd)
        seed_managed(root)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return runner


async def test_provision_installs_when_global_pi_present(root, tmp_path, monkeypatch):
    fake_pi(tmp_path / "bin" / "pi", "0.87.1")
    fake_pi(tmp_path / "bin" / "npm")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    calls = []
    exe = await managed.ensure_managed_pi(runner=installing_runner(root, calls))
    assert exe == managed.managed_pi_executable()
    assert calls and calls[0][:3] == ["npm", "install", "--prefix"]


async def test_provision_is_noop_when_installed(root):
    seed_managed(root)

    def runner(cmd, **kwargs):
        raise AssertionError("must not reinstall")

    assert await managed.ensure_managed_pi(runner=runner) is not None


async def test_provision_skips_machines_without_pi(root, tmp_path, monkeypatch):
    fake_pi(tmp_path / "bin" / "npm")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    def runner(cmd, **kwargs):
        raise AssertionError("no pi user on this machine")

    assert await managed.ensure_managed_pi(runner=runner) is None
    monkeypatch.setenv(managed.PROVISION_ENV, "1")
    calls = []
    assert await managed.ensure_managed_pi(runner=installing_runner(root, calls)) is not None


async def test_provision_can_be_disabled(root, tmp_path, monkeypatch):
    fake_pi(tmp_path / "bin" / "pi")
    fake_pi(tmp_path / "bin" / "npm")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv(managed.PROVISION_ENV, "0")

    def runner(cmd, **kwargs):
        raise AssertionError("disabled")

    assert await managed.ensure_managed_pi(runner=runner) is None


async def test_provision_failure_is_not_fatal(root, tmp_path, monkeypatch):
    fake_pi(tmp_path / "bin" / "pi")
    fake_pi(tmp_path / "bin" / "npm")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "npm ERR! network")

    assert await managed.ensure_managed_pi(runner=runner) is None


async def test_provision_without_npm_is_not_fatal(root, tmp_path, monkeypatch):
    fake_pi(tmp_path / "bin" / "pi")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    def runner(cmd, **kwargs):
        raise AssertionError("npm is missing")

    assert await managed.ensure_managed_pi(runner=runner) is None

