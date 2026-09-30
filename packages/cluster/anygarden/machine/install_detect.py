"""Runtime detection of how the machine daemon was installed (#556).

self-update must work whether the daemon was installed via the
``install.sh`` self-owned venv (pip), ``uv tool install``, or a plain
``pip install "anygarden[machine]"``. Each needs a different update *tool*
and a different *target* distribution, so we resolve the current
environment to a :class:`ResolvedInstall` that
:func:`anygarden.machine.updater.build_update_command` turns into an argv.

Strategy — **manifest-first, detection-fallback**. A manifest is written
only by ``install.sh`` / ``anygarden machine bootstrap``; its *absence* is
itself the signal that this is a non-bootstrap (uv tool / plain pip)
install, so no extra state is needed to tell the two worlds apart.

Security: the update *target* is always chosen from a fixed constant set
(:data:`UMBRELLA_PACKAGE` / :data:`MACHINE_PACKAGE`); nothing here is
derived from user or server input.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from anygarden.machine import install_manifest
from anygarden.machine.install_manifest import InstallManifest

# ── update targets — CONSTANTS (never sourced from user/server input) ──
# The distribution installed by ``uv tool install "anygarden[…]"`` and
# ``pip install "anygarden[…]"``. The daemon ships inside it (#754).
UMBRELLA_PACKAGE = "anygarden"
# What the self-owned venv (install.sh) holds: the daemon extra only.
MACHINE_PACKAGE = install_manifest.PACKAGE_NAME
# #754 — the standalone daemon distribution before the merge. Manifests
# written by older bootstraps still record it; it is never installed.
LEGACY_MACHINE_PACKAGE = "anygarden-machine"

# ── install methods the updater knows how to drive ────────────────────
METHOD_VENV_PIP = "venv-pip"          # self-owned venv (install.sh): pip + machine extra
METHOD_PIP_UMBRELLA = "pip-umbrella"  # plain pip venv:              pip + umbrella pkg
METHOD_UV_TOOL = "uv-tool"            # uv tool install:             uv  + umbrella pkg

# ``uv tool dir`` should answer fast; cap it so a wedged uv never hangs
# the daemon's update path.
_UV_TOOL_DIR_TIMEOUT = 10  # seconds


@dataclass(frozen=True)
class ResolvedInstall:
    """How to update this install: which tool, which distribution.

    ``python`` is the interpreter used by the pip-based methods; it is
    ignored by the uv-tool method (which shells out to ``uv``).
    """

    method: str
    python: str
    package: str
    index_url: str | None = None


def resolve_install(manifest: InstallManifest | None = None) -> ResolvedInstall:
    """Resolve the current install into an update strategy.

    Order:
      1. ``manifest`` present → trust it (bootstrap determinism), mapping
         the retired ``anygarden-machine`` package onto the machine extra.
      2. uv tool install      → :data:`METHOD_UV_TOOL`,      umbrella pkg.
      3. pip present          → :data:`METHOD_PIP_UMBRELLA`, umbrella pkg.
      4. otherwise            → ``ValueError`` (unsupported install).

    Raises ``ValueError`` when no supported method matches.
    """
    if manifest is not None:
        return ResolvedInstall(
            method=manifest.method,
            python=manifest.python,
            package=(
                MACHINE_PACKAGE
                if manifest.package == LEGACY_MACHINE_PACKAGE
                else manifest.package
            ),
            index_url=manifest.index_url,
        )

    python = sys.executable

    # uv tool is checked before pip: a uv-managed venv is the source of
    # truth even if someone slipped pip into it via ensurepip.
    if _is_uv_tool_install(python):
        return ResolvedInstall(
            method=METHOD_UV_TOOL, python=python, package=UMBRELLA_PACKAGE
        )

    if _has_pip():
        return ResolvedInstall(
            method=METHOD_PIP_UMBRELLA, python=python, package=UMBRELLA_PACKAGE
        )

    raise ValueError(
        "unsupported install method: no pip module and not a uv tool install; "
        "update anygarden manually"
    )


# ── detection helpers ─────────────────────────────────────────────────


def _uv_tool_root() -> Path | None:
    """Best-effort root dir of uv-managed tools, or ``None``.

    Resolution order: ``UV_TOOL_DIR`` env → ``uv tool dir`` command →
    the platform default (``~/.local/share/uv/tools``).
    """
    env = os.environ.get("UV_TOOL_DIR")
    if env:
        return Path(env)

    uv = shutil.which("uv")
    if uv:
        try:
            proc = subprocess.run(
                [uv, "tool", "dir"],
                capture_output=True,
                text=True,
                timeout=_UV_TOOL_DIR_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            proc = None
        if proc is not None and proc.returncode == 0:
            out = proc.stdout.strip()
            if out:
                return Path(out)

    return Path.home() / ".local" / "share" / "uv" / "tools"


def _is_uv_tool_install(python: str) -> bool:
    """True if ``python`` lives under the uv tool root."""
    root = _uv_tool_root()
    if root is None:
        return False
    return Path(python).resolve().is_relative_to(root.resolve())


def _has_pip() -> bool:
    """True if the running interpreter can invoke ``pip``."""
    return importlib.util.find_spec("pip") is not None
