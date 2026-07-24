"""Self-update primitive for the machine daemon (#550, #556).

A single code path shared by the manual ``anygarden-machine update``
command and the server-driven ``self_update`` frame. The update strategy
— which tool and which target distribution — is resolved from the
environment by :mod:`anygarden_machine.install_detect`: the self-owned
venv (pip + ``anygarden-machine``), a ``uv tool`` install (uv + umbrella
``anygarden``), or a plain pip umbrella install. A recorded install
manifest still takes precedence, keeping the ``install.sh`` path
deterministic.

Security: the installed distribution is ALWAYS a fixed constant chosen by
the detector (``anygarden`` / ``anygarden-machine``); the optional target
version is validated as PEP 440 before any subprocess runs. Nothing from
the server becomes a package name, source, or shell string — the command
is an argv list (no shell) built entirely from constants plus a validated
version.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import structlog
from packaging.version import InvalidVersion, Version

from anygarden_machine import __version__
from anygarden_machine.install_detect import (
    METHOD_PIP_UMBRELLA,
    METHOD_UV_TOOL,
    METHOD_VENV_PIP,
    ResolvedInstall,
    resolve_install,
)
from anygarden_machine.install_manifest import load as load_manifest

log = structlog.get_logger()

# pip/uv can download the package + dependency closure; give it room but
# never hang the daemon forever.
UPDATE_TIMEOUT = 300  # seconds

# Install methods build_update_command knows how to drive. A new method
# must be added here (and to install_detect) deliberately.
_SUPPORTED_METHODS = frozenset(
    {METHOD_VENV_PIP, METHOD_PIP_UMBRELLA, METHOD_UV_TOOL}
)

# Type of the injectable subprocess runner (for testing).
Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class UpdateResult:
    """Outcome of a self-update attempt (never raised — always returned)."""

    ok: bool
    from_version: str
    # The pinned target when one was requested; the authoritative new
    # version is confirmed by the post-restart ``register`` (daemon_version).
    to_version: str | None
    error: str | None


def _validate_target(target_version: str | None) -> None:
    """Raise ValueError unless ``target_version`` is None or a PEP 440 version."""
    if target_version is None:
        return
    try:
        Version(target_version)
    except InvalidVersion as exc:
        raise ValueError(f"invalid target version: {target_version!r}") from exc


def _pip_command(
    python: str, package: str, index_url: str | None, target_version: str | None
) -> list[str]:
    """``<python> -m pip install --upgrade <package>[==<version>]``."""
    spec = package
    if target_version is not None:
        spec = f"{package}=={target_version}"
    cmd = [python, "-m", "pip", "install", "--upgrade", spec]
    if index_url:
        cmd += ["--index-url", index_url]
    return cmd


def _uv_tool_command(package: str, target_version: str | None) -> list[str]:
    """``uv tool upgrade <package>`` — or a pinned force-reinstall.

    ``uv tool upgrade`` has no version-pin flag, so a pinned target is
    applied via ``uv tool install <package>==<version> --force``.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("uv binary not found on PATH")
    if target_version is None:
        return [uv, "tool", "upgrade", package]
    return [uv, "tool", "install", f"{package}=={target_version}", "--force"]


def build_update_command(
    install: ResolvedInstall, target_version: str | None
) -> list[str]:
    """Build the argv that updates ``install`` for its method.

    Always targets the fixed ``install.package`` (a detector-chosen
    constant). A ``target_version``, if given, must be a valid PEP 440
    version and is pinned with ``==``.

    Raises ``ValueError`` on an invalid version or an unsupported install
    method.
    """
    _validate_target(target_version)

    if install.method not in _SUPPORTED_METHODS:
        raise ValueError(f"unsupported install method: {install.method!r}")

    if install.method == METHOD_UV_TOOL:
        return _uv_tool_command(install.package, target_version)
    return _pip_command(
        install.python, install.package, install.index_url, target_version
    )


def run_update(
    target_version: str | None = None,
    *,
    install: ResolvedInstall | None = None,
    runner: Runner = subprocess.run,
) -> UpdateResult:
    """Update anygarden-machine for the current install method.

    Resolves the install method (unless ``install`` is injected), builds
    the argv, and runs it. Returns an :class:`UpdateResult`; expected
    failures (invalid version, non-zero exit, subprocess error) are
    captured, not raised. On success the new files are on disk but the
    *current* process still runs the old code — the caller (daemon) exits
    so the supervisor restarts on the new version.

    ``install`` and ``runner`` are injectable for tests.
    """
    if install is None:
        install = resolve_install(load_manifest())
    from_version = __version__

    try:
        cmd = build_update_command(install, target_version)
    except ValueError as exc:
        return UpdateResult(False, from_version, None, str(exc))

    log.info(
        "self_update.start",
        from_version=from_version,
        target=target_version,
        method=install.method,
    )
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=UPDATE_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("self_update.subprocess_failed", error=str(exc))
        return UpdateResult(False, from_version, None, f"install failed: {exc}")

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        log.warning(
            "self_update.command_failed", returncode=proc.returncode, tail=tail
        )
        return UpdateResult(
            False,
            from_version,
            None,
            f"update command exited {proc.returncode}: {tail}",
        )

    log.info("self_update.installed", target=target_version, method=install.method)
    return UpdateResult(True, from_version, target_version, None)
