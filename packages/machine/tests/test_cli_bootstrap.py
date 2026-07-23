"""Tests for the ``anygarden-machine bootstrap`` command (#550).

Bootstrap records the running venv as the self-owned install: it writes
the install manifest, a launcher shim on PATH, and the systemd unit — the
pieces that make ``anygarden-machine update`` deterministic.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from anygarden_machine import cli, install_manifest
from anygarden_machine.cli import main


def _setup_home(monkeypatch, tmp_path: Path, fake_python: str) -> Path:
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        install_manifest, "MANIFEST_PATH", tmp_path / ".anygarden" / "machine" / "install.json"
    )
    monkeypatch.setattr(cli.sys, "executable", fake_python)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return tmp_path


def test_bootstrap_writes_manifest(monkeypatch, tmp_path) -> None:
    home = _setup_home(monkeypatch, tmp_path, "/fake/venv/bin/python")
    result = CliRunner().invoke(main, ["bootstrap"])
    assert result.exit_code == 0, result.output

    manifest = install_manifest.load(path=home / ".anygarden" / "machine" / "install.json")
    assert manifest is not None
    assert manifest.method == "venv-pip"
    assert manifest.package == "anygarden-machine"
    assert manifest.python == "/fake/venv/bin/python"


def test_bootstrap_writes_launcher_shim(monkeypatch, tmp_path) -> None:
    home = _setup_home(monkeypatch, tmp_path, "/fake/venv/bin/python")
    CliRunner().invoke(main, ["bootstrap"])

    shim = home / ".local" / "bin" / "anygarden-machine"
    assert shim.exists()
    body = shim.read_text()
    # Shim execs the venv's console script, isolating the environment.
    assert "/fake/venv/bin/anygarden-machine" in body
    assert "unset PYTHONHOME" in body
    # Executable bit set.
    assert shim.stat().st_mode & 0o111


def test_bootstrap_writes_systemd_unit(monkeypatch, tmp_path) -> None:
    home = _setup_home(monkeypatch, tmp_path, "/fake/venv/bin/python")
    CliRunner().invoke(main, ["bootstrap"])

    unit = home / ".config" / "systemd" / "user" / "anygarden-machine.service"
    assert unit.exists()
    text = unit.read_text()
    assert "/fake/venv/bin/python -m anygarden_machine.cli run" in text
