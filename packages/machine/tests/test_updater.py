"""Tests for the self-update primitive (#550, #556).

Security-critical invariants:
  * The (re)installed distribution is ALWAYS a fixed constant
    (``anygarden`` / ``anygarden-machine``) — never taken from server input.
  * The optional target version must be a valid PEP 440 version; anything
    else is rejected before any subprocess runs.
"""

from __future__ import annotations

import subprocess

import pytest

from anygarden_machine import __version__, updater
from anygarden_machine.install_detect import (
    METHOD_PIP_UMBRELLA,
    METHOD_UV_TOOL,
    METHOD_VENV_PIP,
    ResolvedInstall,
)

_VENV_PIP = ResolvedInstall(
    method=METHOD_VENV_PIP,
    python="/home/x/.anygarden/machine/venv/bin/python",
    package="anygarden-machine",
)
_UMBRELLA = ResolvedInstall(
    method=METHOD_PIP_UMBRELLA,
    python="/home/x/venv/bin/python",
    package="anygarden",
)
_UV_TOOL = ResolvedInstall(
    method=METHOD_UV_TOOL, python="/ignored", package="anygarden"
)


def _with_uv(monkeypatch: pytest.MonkeyPatch, path: str | None = "/usr/bin/uv") -> None:
    monkeypatch.setattr(
        updater.shutil, "which", lambda n: path if n == "uv" else None
    )


# ── build_update_command: pip methods ─────────────────────────────────


def test_venv_pip_command_targets_machine_pkg() -> None:
    cmd = updater.build_update_command(_VENV_PIP, None)
    assert cmd == [
        "/home/x/.anygarden/machine/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "anygarden-machine",
    ]


def test_pip_umbrella_command_targets_umbrella_pkg() -> None:
    cmd = updater.build_update_command(_UMBRELLA, None)
    assert cmd == [
        "/home/x/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "anygarden",
    ]


def test_pip_target_version_is_pinned() -> None:
    cmd = updater.build_update_command(_VENV_PIP, "0.14.0")
    assert cmd[-1] == "anygarden-machine==0.14.0"


def test_index_url_appended() -> None:
    m = ResolvedInstall(
        method=METHOD_VENV_PIP,
        python="/p",
        package="anygarden-machine",
        index_url="https://pypi.example.com/simple",
    )
    cmd = updater.build_update_command(m, None)
    assert "--index-url" in cmd
    assert "https://pypi.example.com/simple" in cmd


# ── build_update_command: uv tool ─────────────────────────────────────


def test_uv_tool_upgrade_command(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_uv(monkeypatch)
    cmd = updater.build_update_command(_UV_TOOL, None)
    assert cmd == ["/usr/bin/uv", "tool", "upgrade", "anygarden"]


def test_uv_tool_pinned_uses_force_install(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_uv(monkeypatch)
    cmd = updater.build_update_command(_UV_TOOL, "0.14.0")
    assert cmd == ["/usr/bin/uv", "tool", "install", "anygarden==0.14.0", "--force"]


def test_uv_tool_missing_uv_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_uv(monkeypatch, path=None)
    with pytest.raises(ValueError):
        updater.build_update_command(_UV_TOOL, None)


# ── security invariants (across methods) ──────────────────────────────


def test_invalid_target_version_rejected() -> None:
    with pytest.raises(ValueError):
        updater.build_update_command(_VENV_PIP, "not-a-version")


def test_target_cannot_smuggle_a_package_name() -> None:
    # A package name (not a version) must be rejected — the package is fixed.
    with pytest.raises(ValueError):
        updater.build_update_command(_VENV_PIP, "requests")
    # Shell-ish payloads are not valid PEP 440 versions either.
    with pytest.raises(ValueError):
        updater.build_update_command(_VENV_PIP, "0.1.0; rm -rf /")


def test_uv_tool_target_smuggle_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_uv(monkeypatch)
    with pytest.raises(ValueError):
        updater.build_update_command(_UV_TOOL, "0.1.0; rm -rf /")


def test_unsupported_method_rejected() -> None:
    m = ResolvedInstall(method="conda", python="/p", package="anygarden-machine")
    with pytest.raises(ValueError):
        updater.build_update_command(m, None)


# ── run_update ────────────────────────────────────────────────────────


def _fake_runner(returncode: int, stderr: str = ""):
    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    return runner


def test_run_update_success() -> None:
    result = updater.run_update(None, install=_VENV_PIP, runner=_fake_runner(0))
    assert result.ok is True
    assert result.from_version == __version__
    assert result.error is None


def test_run_update_pip_failure_reports_error() -> None:
    result = updater.run_update(
        None,
        install=_VENV_PIP,
        runner=_fake_runner(1, stderr="No matching distribution"),
    )
    assert result.ok is False
    assert "No matching distribution" in result.error


def test_run_update_invalid_target_no_subprocess() -> None:
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    result = updater.run_update("bogus", install=_VENV_PIP, runner=runner)
    assert result.ok is False
    assert calls == []  # rejected before running anything


def test_run_update_subprocess_error_handled() -> None:
    def runner(cmd, **kwargs):
        raise OSError("boom")

    result = updater.run_update(None, install=_VENV_PIP, runner=runner)
    assert result.ok is False
    assert "boom" in result.error


def test_run_update_resolves_install_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No explicit install → resolve_install(load_manifest()) is consulted.
    monkeypatch.setattr(updater, "load_manifest", lambda: None)
    monkeypatch.setattr(updater, "resolve_install", lambda _m: _VENV_PIP)
    result = updater.run_update(None, runner=_fake_runner(0))
    assert result.ok is True
