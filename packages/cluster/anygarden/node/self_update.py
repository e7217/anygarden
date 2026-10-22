"""Update the integrated node from the web UI (#927).

The machine daemon updates itself and exits so systemd restarts it (#550).
An integrated node (``anygarden start``) cannot do that: the API and its
local machine share one process, and most nodes run in a terminal with no
supervisor. So the node:

1. installs the new release while it keeps serving (a failure leaves the
   running version untouched),
2. shuts down the normal way (the same path as ``anygarden stop``), and
3. re-executes itself with the same arguments and launch environment
   (``os.execve`` — same PID, same terminal; see ``cli.start_node``).

The new build then applies pending migrations at startup (#920).

Security: the installed requirement is the fixed ``anygarden[server,agent]``
pinned to a validated PEP 440 version; the command is an argv list (no shell).
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import structlog
from packaging.version import InvalidVersion, Version

from anygarden.machine.install_detect import (
    METHOD_PIP_UMBRELLA,
    METHOD_UV_TOOL,
    ResolvedInstall,
    resolve_install,
)
from anygarden.machine.updater import UPDATE_TIMEOUT

log = structlog.get_logger()

# What a node installs: the API plus the agent runtime its local machine spawns.
NODE_REQUIREMENT = "anygarden[server,agent]"

# Why the web update is unavailable (shown by the UI; stable API values).
NOT_INTEGRATED_NODE = "not_integrated_node"
UNSUPPORTED_PLATFORM = "unsupported_platform"
DEVELOPMENT_INSTALL = "development_install"
UNSUPPORTED_INSTALL = "unsupported_install"

Runner = Callable[..., subprocess.CompletedProcess]


class NodeUpdateRejected(ValueError):
    """The requested update cannot run (bad or non-newer version)."""


class NodeUpdateBusy(RuntimeError):
    """An update is already installing or restarting the node."""


def _pinned(target_version: str) -> str:
    try:
        Version(target_version)
    except InvalidVersion as exc:
        raise ValueError(f"invalid target version: {target_version!r}") from exc
    return f"{NODE_REQUIREMENT}=={target_version}"


def build_node_update_command(install: ResolvedInstall, target_version: str) -> list[str]:
    """Return the argv that installs ``target_version`` for this node's install.

    Raises ``ValueError`` on an invalid version or an install the node cannot
    drive (a self-owned machine venv never hosts a node).
    """
    spec = _pinned(target_version)
    if install.method == METHOD_UV_TOOL:
        uv = shutil.which("uv")
        if uv is None:
            raise ValueError("uv binary not found on PATH")
        # ``uv tool upgrade`` cannot pin; a forced install replaces the tool env.
        return [uv, "tool", "install", spec, "--force"]
    if install.method == METHOD_PIP_UMBRELLA:
        return [install.python, "-m", "pip", "install", "--upgrade", spec]
    raise ValueError(f"unsupported install method: {install.method!r}")


def is_development_install(distribution: str = "anygarden") -> bool:
    """True for a source checkout (editable or local directory install).

    Installing from PyPI over it would silently replace the developer's code.
    """
    try:
        raw = importlib.metadata.distribution(distribution).read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        return True
    if not raw:
        return False
    try:
        info = json.loads(raw)
    except ValueError:
        return False
    if info.get("dir_info", {}).get("editable"):
        return True
    return "dir_info" in info


def unsupported_reason(
    *,
    integrated: bool,
    platform: str | None = None,
    editable: bool | None = None,
    resolve: Callable[[], ResolvedInstall] = resolve_install,
) -> str | None:
    """Return why this process cannot update itself, or ``None`` when it can."""
    if not integrated:
        return NOT_INTEGRATED_NODE
    # ``os.execve`` replaces the process only on POSIX.
    if (platform or os.name) != "posix":
        return UNSUPPORTED_PLATFORM
    if editable if editable is not None else is_development_install():
        return DEVELOPMENT_INSTALL
    try:
        build_node_update_command(resolve(), "0")
    except ValueError:
        return UNSUPPORTED_INSTALL
    return None


def reexec_command(argv: list[str] | None = None, executable: str | None = None) -> list[str]:
    """The command that restarts this ``anygarden start`` on the installed code.

    ``python -m anygarden`` does not depend on the console-script path, which
    a reinstall may rewrite.
    """
    argv = sys.argv if argv is None else argv
    return [executable or sys.executable, "-m", "anygarden", *argv[1:]]


class NodeUpdater:
    """One in-process update at a time: ``idle → installing → restarting``.

    A failed install ends in ``failed`` and the node keeps running; the state
    lives in memory, so after the re-exec the new process starts ``idle`` and
    the UI confirms success by the reported version.
    """

    def __init__(
        self,
        *,
        current_version: str,
        request_restart: Callable[[], None],
        resolve: Callable[[], ResolvedInstall] = resolve_install,
        runner: Runner = subprocess.run,
    ) -> None:
        self._current = current_version
        self._request_restart = request_restart
        self._resolve = resolve
        self._runner = runner
        self._status = "idle"
        self._target: str | None = None
        self._error: str | None = None
        self._task: asyncio.Task | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self._status,
            "from_version": self._current,
            "target_version": self._target,
            "error": self._error,
        }

    def start(self, target_version: str) -> asyncio.Task:
        """Validate, then install in the background; raises before any work."""
        if self._status in ("installing", "restarting"):
            raise NodeUpdateBusy(f"node update already {self._status}")
        try:
            target = Version(target_version)
        except InvalidVersion as exc:
            raise NodeUpdateRejected(f"invalid target version: {target_version!r}") from exc
        try:
            newer = target > Version(self._current)
        except InvalidVersion:
            newer = True
        if not newer:
            # The older build would refuse a database already migrated past
            # its head (#920) and the node would not come back.
            raise NodeUpdateRejected(
                f"{target_version} is not newer than the running {self._current}"
            )
        self._status = "installing"
        self._target = target_version
        self._error = None
        self._task = asyncio.create_task(self._run(target_version), name="node-self-update")
        return self._task

    async def _run(self, target_version: str) -> None:
        error = await self._install(target_version)
        if error is not None:
            self._status = "failed"
            self._error = error
            log.warning("node_update.failed", target=target_version, error=error)
            return
        self._status = "restarting"
        log.info("node_update.installed_restarting", target=target_version)
        self._request_restart()

    async def _install(self, target_version: str) -> str | None:
        """Run the installer off the event loop; return an error or ``None``."""
        try:
            cmd = build_node_update_command(self._resolve(), target_version)
        except ValueError as exc:
            return str(exc)
        log.info("node_update.start", from_version=self._current, target=target_version)
        try:
            proc = await asyncio.to_thread(
                self._runner, cmd, capture_output=True, text=True, timeout=UPDATE_TIMEOUT
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"install failed: {exc}"
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            return f"update command exited {proc.returncode}: {tail}"
        return None
