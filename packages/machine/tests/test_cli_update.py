"""Tests for the ``anygarden-machine update`` CLI command (#550)."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from anygarden_machine.cli import main
from anygarden_machine.updater import UpdateResult


def _ok(target=None):
    return UpdateResult(ok=True, from_version="0.12.0", to_version=target, error=None)


def _fail(error="boom"):
    return UpdateResult(ok=False, from_version="0.12.0", to_version=None, error=error)


def test_update_success_prints_restart_hint() -> None:
    with patch("anygarden_machine.cli.run_update", return_value=_ok()) as ru:
        result = CliRunner().invoke(main, ["update"])
    assert result.exit_code == 0, result.output
    ru.assert_called_once()
    assert "systemctl --user restart anygarden-machine" in result.output


def test_update_passes_target_version() -> None:
    with patch("anygarden_machine.cli.run_update", return_value=_ok("0.13.0")) as ru:
        result = CliRunner().invoke(main, ["update", "--version", "0.13.0"])
    assert result.exit_code == 0
    assert ru.call_args.args[0] == "0.13.0"


def test_update_failure_exits_nonzero() -> None:
    with patch(
        "anygarden_machine.cli.run_update",
        return_value=_fail("No matching distribution"),
    ):
        result = CliRunner().invoke(main, ["update"])
    assert result.exit_code == 1
    assert "No matching distribution" in result.output


def test_update_restart_flag_invokes_systemctl() -> None:
    with patch("anygarden_machine.cli.run_update", return_value=_ok()), patch(
        "anygarden_machine.cli.subprocess.run"
    ) as sp:
        result = CliRunner().invoke(main, ["update", "--restart"])
    assert result.exit_code == 0
    argv = sp.call_args.args[0]
    assert argv[:2] == ["systemctl", "--user"]
    assert "restart" in argv and "anygarden-machine" in argv
