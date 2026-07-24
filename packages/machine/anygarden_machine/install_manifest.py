"""Self-owned install manifest for the machine daemon (#550).

The bootstrap installer creates a dedicated venv at ``~/.anygarden/machine/``
and records *how* it was installed here. ``anygarden-machine update`` then
reinstalls deterministically from this manifest — no pip/uv/pipx detection.

``load`` returns ``None`` for any absent or malformed manifest so a
non-bootstrap install (plain ``pip install anygarden-machine`` somewhere)
degrades to a best-effort update path rather than crashing.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from anygarden_machine.config import ANYGARDEN_DIR

# The self-owned install root the bootstrap installer creates. Distinct
# from the ``machine.toml`` config file that lives directly under
# ``~/.anygarden`` — a directory named ``machine`` does not collide with
# a file named ``machine.toml``.
INSTALL_ROOT = ANYGARDEN_DIR / "machine"
VENV_DIR = INSTALL_ROOT / "venv"
MANIFEST_PATH = INSTALL_ROOT / "install.json"

# The distribution name the updater always (re)installs. Never sourced
# from user/server input — see updater.build_update_command.
PACKAGE_NAME = "anygarden-machine"


class InstallManifest(BaseModel):
    """Records the self-owned install layout and update method."""

    # ``install.sh``/``bootstrap`` record "venv-pip" (``python -m venv`` + pip).
    # With no manifest, install_detect resolves the method at runtime
    # ("uv-tool" / "pip-umbrella", #556); the field lets new install methods
    # extend without a schema change.
    method: str
    # The distribution this owned install updates (``anygarden-machine``) —
    # recorded for transparency/debugging.
    package: str
    # Absolute path to the owned venv's interpreter; the updater runs
    # ``<python> -m pip install -U`` against exactly this environment.
    python: str
    # Package index to install from; ``None`` means the default PyPI.
    index_url: str | None = None


def load(path: Path | None = None) -> InstallManifest | None:
    """Load the manifest, or ``None`` if absent/malformed."""
    manifest_path = path or MANIFEST_PATH
    try:
        raw = manifest_path.read_text()
    except OSError:
        return None
    try:
        return InstallManifest.model_validate_json(raw)
    except ValidationError:
        return None
    except json.JSONDecodeError:
        return None


def write(manifest: InstallManifest, path: Path | None = None) -> None:
    """Write the manifest to disk, creating parent directories."""
    manifest_path = path or MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n")
