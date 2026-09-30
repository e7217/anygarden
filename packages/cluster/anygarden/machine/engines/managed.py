"""AnyGarden-managed, version-pinned engine installs (#688).

Pi agents used to run whatever ``pi`` was first on ``PATH``. The agent adapter
is verified against one exact Pi version (event stream, isolation flags,
``models.json`` schema, ``PI_*`` env names), so a routine global upgrade —
including this machine's own *Update engine* action — silently took every Pi
agent offline with ``UNSUPPORTED_RUNTIME``.

The machine now owns a private install per pinned version under
``~/.anygarden/engines/pi-cli/<version>/`` and hands its executable to agents
via ``ANYGARDEN_PI_EXECUTABLE``. The operator's global ``pi`` is used only
with the explicit ``ANYGARDEN_PI_USE_PATH=1`` opt-in — never as a silent
fallback. Upgrading Pi is a code change: bump ``PI_PINNED_VERSION`` together
with the agent adapter's ``ENGINE_VERSION`` (a test keeps them equal).

Environment:
  ``ANYGARDEN_MANAGED_ENGINES_DIR`` — override the managed root (air-gapped
      nodes can pre-seed ``<root>/pi-cli/<version>/node_modules``).
  ``ANYGARDEN_MANAGED_PI`` — ``0`` disables startup provisioning, ``1``
      provisions even when no global ``pi`` is present.
  ``ANYGARDEN_PI_USE_PATH`` — ``1`` lets detection and agents use ``pi``
      from ``PATH`` when no managed install exists.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import structlog

log = structlog.get_logger()

PI_PACKAGE = "@earendil-works/pi-coding-agent"
PI_PINNED_VERSION = "0.85.1"

ROOT_ENV = "ANYGARDEN_MANAGED_ENGINES_DIR"
PROVISION_ENV = "ANYGARDEN_MANAGED_PI"
PATH_OPT_IN_ENV = "ANYGARDEN_PI_USE_PATH"
EXECUTABLE_ENV = "ANYGARDEN_PI_EXECUTABLE"

INSTALL_TIMEOUT = 300  # seconds

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def managed_root() -> Path:
    override = os.environ.get(ROOT_ENV)
    return Path(override) if override else Path.home() / ".anygarden" / "engines"


def managed_prefix(version: str = PI_PINNED_VERSION) -> Path:
    return managed_root() / "pi-cli" / version


def managed_pi_executable(version: str = PI_PINNED_VERSION) -> Path | None:
    """The managed ``pi`` shim when installed and executable, else ``None``."""
    path = managed_prefix(version) / "node_modules" / ".bin" / "pi"
    return path if path.is_file() and os.access(path, os.X_OK) else None


def pi_path_opt_in() -> bool:
    return os.environ.get(PATH_OPT_IN_ENV) == "1"


def install_argv(prefix: Path, version: str = PI_PINNED_VERSION) -> list[str]:
    """argv (no shell) installing the pinned Pi into ``prefix``."""
    return [
        "npm",
        "install",
        "--prefix",
        str(prefix),
        "--no-audit",
        "--no-fund",
        f"{PI_PACKAGE}@{version}",
    ]


async def ensure_managed_pi(runner: Runner = subprocess.run) -> Path | None:
    """Provision the pinned Pi once at daemon start; never raises.

    Only machines that already use Pi (a global ``pi`` on ``PATH``) or that
    opt in with ``ANYGARDEN_MANAGED_PI=1`` are provisioned, so machines
    without Pi don't get an unexpected npm install.
    """
    existing = managed_pi_executable()
    if existing is not None:
        return existing
    mode = os.environ.get(PROVISION_ENV)
    if mode == "0" or (mode != "1" and shutil.which("pi") is None):
        return None
    if shutil.which("npm") is None:
        log.warning("managed_pi.npm_missing", version=PI_PINNED_VERSION)
        return None
    prefix = managed_prefix()
    cmd = install_argv(prefix)
    log.info("managed_pi.install_start", prefix=str(prefix), version=PI_PINNED_VERSION)

    def install() -> subprocess.CompletedProcess[str]:
        prefix.mkdir(parents=True, exist_ok=True)
        return runner(cmd, capture_output=True, text=True, timeout=INSTALL_TIMEOUT)

    try:
        proc = await asyncio.to_thread(install)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("managed_pi.install_failed", error=str(exc))
        return None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        log.warning("managed_pi.install_failed", returncode=proc.returncode, tail=tail)
        return None
    installed = managed_pi_executable()
    log.info("managed_pi.install_done", executable=str(installed))
    return installed
