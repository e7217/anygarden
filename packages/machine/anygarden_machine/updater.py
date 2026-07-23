"""Self-update primitive for the machine daemon (#550).

A single code path shared by the manual ``anygarden-machine update``
command and the server-driven ``self_update`` frame. It is *deterministic*
because the bootstrap installer owns the venv and records the method in the
install manifest — there is no pip/uv/pipx detection.

Security: the installed distribution is ALWAYS the fixed
``anygarden-machine`` from the manifest's index; the optional target version
is validated as PEP 440 before any subprocess runs. Nothing from the server
becomes a package name, source, or shell string — the command is an argv
list (no shell) built entirely from constants plus a validated version.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

import structlog
from packaging.version import InvalidVersion, Version

from anygarden_machine import __version__
from anygarden_machine.install_manifest import (
    PACKAGE_NAME,
    InstallManifest,
)
from anygarden_machine.install_manifest import load as load_manifest

log = structlog.get_logger()

# pip install can download the package + dependency closure; give it room
# but never hang the daemon forever.
UPDATE_TIMEOUT = 300  # seconds

# Install methods the updater knows how to drive. v1 bootstrap only writes
# "venv-pip"; a new method must be added here deliberately.
_SUPPORTED_METHODS = frozenset({"venv-pip"})

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


def build_update_command(
    manifest: InstallManifest | None, target_version: str | None
) -> list[str]:
    """Build the argv that reinstalls ``anygarden-machine``.

    Always targets the fixed :data:`PACKAGE_NAME`. A ``target_version``, if
    given, must be a valid PEP 440 version and is pinned with ``==``.

    Raises ``ValueError`` on an invalid version or an unsupported install
    method.
    """
    _validate_target(target_version)

    method = manifest.method if manifest else "venv-pip"
    if method not in _SUPPORTED_METHODS:
        raise ValueError(f"unsupported install method: {method!r}")

    # No manifest ⇒ best-effort against the interpreter we're running in
    # (works when that venv has pip; documented limitation otherwise).
    python = manifest.python if manifest else sys.executable

    spec = PACKAGE_NAME
    if target_version is not None:
        spec = f"{PACKAGE_NAME}=={target_version}"

    cmd = [python, "-m", "pip", "install", "--upgrade", spec]
    if manifest and manifest.index_url:
        cmd += ["--index-url", manifest.index_url]
    return cmd


def run_update(
    target_version: str | None = None,
    *,
    manifest: InstallManifest | None = None,
    runner: Runner = subprocess.run,
) -> UpdateResult:
    """Reinstall ``anygarden-machine`` into the owned venv.

    Returns an :class:`UpdateResult`; expected failures (invalid version,
    non-zero pip, subprocess error) are captured, not raised. On success the
    new files are on disk but the *current* process still runs the old code —
    the caller (daemon) exits so systemd restarts on the new version.

    ``runner`` is injectable for tests.
    """
    if manifest is None:
        manifest = load_manifest()
    from_version = __version__

    try:
        cmd = build_update_command(manifest, target_version)
    except ValueError as exc:
        return UpdateResult(False, from_version, None, str(exc))

    log.info("self_update.start", from_version=from_version, target=target_version)
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=UPDATE_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("self_update.subprocess_failed", error=str(exc))
        return UpdateResult(False, from_version, None, f"install failed: {exc}")

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        log.warning("self_update.pip_failed", returncode=proc.returncode, tail=tail)
        return UpdateResult(
            False, from_version, None, f"pip exited {proc.returncode}: {tail}"
        )

    log.info("self_update.installed", target=target_version)
    return UpdateResult(True, from_version, target_version, None)
