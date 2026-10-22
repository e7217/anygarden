"""Updating the integrated node from the web UI (#927).

The node installs the new release while it keeps serving, then shuts down
the normal way and re-executes itself in the same process (same PID, same
terminal), so a foreground ``anygarden start`` needs no supervisor.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest
from anygarden.machine.install_detect import (
    METHOD_PIP_UMBRELLA,
    METHOD_UV_TOOL,
    ResolvedInstall,
)
from anygarden.node import self_update
from anygarden.node.self_update import (
    NodeUpdateBusy,
    NodeUpdater,
    NodeUpdateRejected,
    build_node_update_command,
    reexec_command,
    unsupported_reason,
)

PIP = ResolvedInstall(method=METHOD_PIP_UMBRELLA, python="/venv/bin/python", package="anygarden")
UV = ResolvedInstall(method=METHOD_UV_TOOL, python="/uv/bin/python", package="anygarden")


def _ok(*args, **kwargs):
    return subprocess.CompletedProcess(args[0], 0, "", "")


def _failing(*args, **kwargs):
    return subprocess.CompletedProcess(args[0], 1, "", "No matching distribution")


# ── install command ───────────────────────────────────────────────────


@pytest.mark.req("NODE-17")
def test_pip_install_pins_the_node_extras_and_version():
    assert build_node_update_command(PIP, "0.24.0") == [
        "/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "anygarden[server,agent]==0.24.0",
    ]


def test_uv_tool_install_pins_the_node_extras_and_version(monkeypatch):
    monkeypatch.setattr(self_update.shutil, "which", lambda name: "/bin/uv")
    assert build_node_update_command(UV, "0.24.0") == [
        "/bin/uv",
        "tool",
        "install",
        "anygarden[server,agent]==0.24.0",
        "--force",
    ]


@pytest.mark.parametrize("bad", ["latest", "0.24.0; rm -rf /", "", "anygarden"])
def test_install_command_rejects_a_non_pep440_version(bad):
    with pytest.raises(ValueError):
        build_node_update_command(PIP, bad)


# ── support ───────────────────────────────────────────────────────────


@pytest.mark.req("NODE-17")
@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"integrated": False}, "not_integrated_node"),
        ({"platform": "nt"}, "unsupported_platform"),
        ({"editable": True}, "development_install"),
        ({"resolve": lambda: (_ for _ in ()).throw(ValueError("no pip"))}, "unsupported_install"),
    ],
)
def test_unsupported_setups_say_why(kwargs, reason):
    args = {
        "integrated": True,
        "platform": "posix",
        "editable": False,
        "resolve": lambda: PIP,
    }
    args.update(kwargs)
    assert unsupported_reason(**args) == reason


def test_a_released_posix_node_install_is_supported():
    assert (
        unsupported_reason(
            integrated=True, platform="posix", editable=False, resolve=lambda: PIP
        )
        is None
    )


# ── re-exec ───────────────────────────────────────────────────────────


@pytest.mark.req("NODE-17")
def test_reexec_runs_the_same_start_arguments_under_this_interpreter():
    argv = ["/home/u/.local/bin/anygarden", "start", "--data-dir", "/d", "--port", "8100"]
    assert reexec_command(argv, "/venv/bin/python") == [
        "/venv/bin/python",
        "-m",
        "anygarden",
        "start",
        "--data-dir",
        "/d",
        "--port",
        "8100",
    ]


# ── updater state machine ─────────────────────────────────────────────


def _updater(runner=_ok, restarts=None):
    restarts = restarts if restarts is not None else []
    return NodeUpdater(
        current_version="0.23.0",
        request_restart=lambda: restarts.append(True),
        resolve=lambda: PIP,
        runner=runner,
    )


@pytest.mark.asyncio
@pytest.mark.req("NODE-17")
async def test_successful_install_requests_a_restart():
    restarts: list[bool] = []
    updater = _updater(restarts=restarts)
    task = updater.start("0.24.0")
    assert updater.snapshot()["status"] == "installing"
    await task
    snap = updater.snapshot()
    assert snap["status"] == "restarting"
    assert snap["from_version"] == "0.23.0"
    assert snap["target_version"] == "0.24.0"
    assert restarts == [True]


@pytest.mark.asyncio
@pytest.mark.req("NODE-17")
async def test_failed_install_keeps_the_node_running_and_reports_why():
    restarts: list[bool] = []
    updater = _updater(runner=_failing, restarts=restarts)
    await updater.start("0.24.0")
    snap = updater.snapshot()
    assert snap["status"] == "failed"
    assert "No matching distribution" in snap["error"]
    assert restarts == []
    # A failed attempt can be retried.
    await updater.start("0.24.0")


@pytest.mark.asyncio
async def test_installer_crash_is_a_failure_not_an_exception():
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 300)

    updater = _updater(runner=boom)
    await updater.start("0.24.0")
    assert updater.snapshot()["status"] == "failed"


@pytest.mark.asyncio
async def test_second_update_while_one_runs_is_refused():
    gate = asyncio.Event()

    def slow(*args, **kwargs):
        return _ok(*args)

    updater = _updater(runner=slow)
    original = updater._install

    async def held(target):
        await gate.wait()
        return await original(target)

    updater._install = held
    task = updater.start("0.24.0")
    with pytest.raises(NodeUpdateBusy):
        updater.start("0.24.0")
    gate.set()
    await task


@pytest.mark.parametrize("target", ["0.23.0", "0.22.1"])
def test_only_a_newer_release_is_installed(target):
    # The new build would refuse a database migrated past its own head (#920),
    # so a downgrade could leave the node unable to start.
    with pytest.raises(NodeUpdateRejected):
        _updater().start(target)


def test_invalid_version_is_rejected_before_anything_runs():
    with pytest.raises(NodeUpdateRejected):
        _updater().start("not-a-version")
