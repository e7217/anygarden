"""Tests for the self-update primitive (#550).

Security-critical invariants:
  * The (re)installed distribution is ALWAYS the fixed ``anygarden-machine`` —
    never taken from server input.
  * The optional target version must be a valid PEP 440 version; anything
    else is rejected before any subprocess runs.
"""

from __future__ import annotations

import subprocess

import pytest

from anygarden_machine import __version__, updater
from anygarden_machine.install_manifest import InstallManifest

_MANIFEST = InstallManifest(
    method="venv-pip",
    package="anygarden-machine",
    python="/home/x/.anygarden/machine/venv/bin/python",
)


# ── build_update_command ──────────────────────────────────────────────


def test_command_uses_manifest_python_and_fixed_package() -> None:
    cmd = updater.build_update_command(_MANIFEST, None)
    assert cmd == [
        "/home/x/.anygarden/machine/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "anygarden-machine",
    ]


def test_command_without_manifest_falls_back_to_sys_executable() -> None:
    import sys

    cmd = updater.build_update_command(None, None)
    assert cmd[0] == sys.executable
    assert cmd[-1] == "anygarden-machine"


def test_target_version_is_pinned() -> None:
    cmd = updater.build_update_command(_MANIFEST, "0.13.0")
    assert cmd[-1] == "anygarden-machine==0.13.0"


def test_invalid_target_version_rejected() -> None:
    with pytest.raises(ValueError):
        updater.build_update_command(_MANIFEST, "not-a-version")


def test_target_cannot_smuggle_a_package_name() -> None:
    # A package name (not a version) must be rejected — the package is fixed.
    with pytest.raises(ValueError):
        updater.build_update_command(_MANIFEST, "requests")
    # Shell-ish payloads are not valid PEP 440 versions either.
    with pytest.raises(ValueError):
        updater.build_update_command(_MANIFEST, "0.1.0; rm -rf /")


def test_index_url_appended() -> None:
    m = InstallManifest(
        method="venv-pip",
        package="anygarden-machine",
        python="/p",
        index_url="https://pypi.example.com/simple",
    )
    cmd = updater.build_update_command(m, None)
    assert "--index-url" in cmd
    assert "https://pypi.example.com/simple" in cmd


def test_unsupported_method_rejected() -> None:
    m = InstallManifest(method="conda", package="anygarden-machine", python="/p")
    with pytest.raises(ValueError):
        updater.build_update_command(m, None)


# ── run_update ────────────────────────────────────────────────────────


def _fake_runner(returncode: int, stderr: str = ""):
    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    return runner


def test_run_update_success() -> None:
    result = updater.run_update(None, manifest=_MANIFEST, runner=_fake_runner(0))
    assert result.ok is True
    assert result.from_version == __version__
    assert result.error is None


def test_run_update_pip_failure_reports_error() -> None:
    result = updater.run_update(
        None, manifest=_MANIFEST, runner=_fake_runner(1, stderr="No matching distribution")
    )
    assert result.ok is False
    assert "No matching distribution" in result.error


def test_run_update_invalid_target_no_subprocess() -> None:
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    result = updater.run_update("bogus", manifest=_MANIFEST, runner=runner)
    assert result.ok is False
    assert calls == []  # rejected before running anything


def test_run_update_subprocess_error_handled() -> None:
    def runner(cmd, **kwargs):
        raise OSError("boom")

    result = updater.run_update(None, manifest=_MANIFEST, runner=runner)
    assert result.ok is False
    assert "boom" in result.error
