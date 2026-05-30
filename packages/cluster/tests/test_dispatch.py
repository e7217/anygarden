"""Tests for the unified ``anygarden`` dispatcher (#396).

The dispatcher routes ``anygarden <server|machine|agent|client>`` to the
matching component CLI, lazy-importing each target so a missing optional
extra surfaces an install hint instead of a traceback.
"""

from __future__ import annotations

import sys
import types

import pytest
from click.testing import CliRunner

from anygarden import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_dispatch_registers_all_subcommands() -> None:
    assert set(cli.dispatch.commands) == {"server", "machine", "agent", "client"}


def test_dispatch_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(cli.dispatch, ["--help"])
    assert result.exit_code == 0
    for sub in ("server", "machine", "agent", "client"):
        assert sub in result.output


# --- delegation -----------------------------------------------------------


def _install_fake_module(monkeypatch, name: str, **attrs) -> dict:
    """Register a fake module under *name* and record delegated calls."""
    calls: dict = {}
    mod = types.ModuleType(name)
    for attr, recorder_key in attrs.items():
        def _make(key):
            def _fn(*args, **kwargs):
                calls[key] = {"args": args, "kwargs": kwargs}
            return _fn
        setattr(mod, attr, _make(recorder_key))
    # ensure parent package exists for ``fromlist`` import
    parent = name.split(".")[0]
    if parent not in sys.modules:
        monkeypatch.setitem(sys.modules, parent, types.ModuleType(parent))
    monkeypatch.setitem(sys.modules, name, mod)
    return calls


def test_machine_delegates_to_machine_cli(runner, monkeypatch) -> None:
    calls = _install_fake_module(monkeypatch, "anygarden_machine.cli", main="machine")
    result = runner.invoke(cli.dispatch, ["machine", "run", "--server", "ws://x"])
    assert result.exit_code == 0
    assert "machine" in calls
    # extra args after the subcommand are passed through to the component CLI
    assert calls["machine"]["kwargs"]["args"] == ["run", "--server", "ws://x"]


def test_agent_delegates_to_agent_cli(runner, monkeypatch) -> None:
    calls = _install_fake_module(
        monkeypatch, "anygarden_agent.cli", agent_main="agent", client_main="client"
    )
    result = runner.invoke(cli.dispatch, ["agent", "--engine", "codex"])
    assert result.exit_code == 0
    assert calls["agent"]["kwargs"]["args"] == ["--engine", "codex"]


def test_client_delegates_to_client_cli(runner, monkeypatch) -> None:
    calls = _install_fake_module(
        monkeypatch, "anygarden_agent.cli", agent_main="agent", client_main="client"
    )
    result = runner.invoke(cli.dispatch, ["client", "--room", "r1"])
    assert result.exit_code == 0
    assert calls["client"]["kwargs"]["args"] == ["--room", "r1"]


# --- missing-extra guards -------------------------------------------------


def test_machine_missing_extra_hint(runner, monkeypatch) -> None:
    # Force the lazy import to fail as if anygarden[machine] were not installed.
    monkeypatch.setitem(sys.modules, "anygarden_machine.cli", None)
    monkeypatch.setitem(sys.modules, "anygarden_machine", None)
    result = runner.invoke(cli.dispatch, ["machine", "run"])
    assert result.exit_code != 0
    assert 'pip install "anygarden[machine]"' in result.output


def test_agent_missing_extra_hint(runner, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "anygarden_agent.cli", None)
    monkeypatch.setitem(sys.modules, "anygarden_agent", None)
    result = runner.invoke(cli.dispatch, ["agent"])
    assert result.exit_code != 0
    assert 'pip install "anygarden[agent]"' in result.output


def test_server_missing_extra_hint(runner, monkeypatch) -> None:
    # Pretend the server stack is absent regardless of what's installed.
    monkeypatch.setattr(cli, "_server_extra_installed", lambda: False)
    result = runner.invoke(cli.dispatch, ["server"])
    assert result.exit_code != 0
    assert 'pip install "anygarden[server]"' in result.output


def test_server_delegates_when_extra_present(runner, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_server_extra_installed", lambda: True)
    recorded: dict = {}

    def _fake_main(*args, **kwargs):
        recorded["called"] = kwargs

    monkeypatch.setattr(cli, "main", _fake_main)
    result = runner.invoke(cli.dispatch, ["server", "--port", "9000"])
    assert result.exit_code == 0
    assert recorded["called"]["args"] == ["--port", "9000"]
