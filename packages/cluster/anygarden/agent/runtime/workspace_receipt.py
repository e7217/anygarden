"""Publish the engine's chosen managed working directory without secrets."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import psutil

RECEIPT_NAME = ".anygarden-workspace.json"


def report_workspace(
    root: Path, workspace: Path, *, generation: int, engine: str, permission_level: str
) -> None:
    """Best effort; reporting must never prevent an otherwise valid turn.

    Only the two existing managed-directory choices are reportable. The
    machine validates the receipt against its process/generation and opens
    every directory component without following links before browsing.
    """
    if workspace not in (root, root / "workspace"):
        return
    temporary: str | None = None
    try:
        data = {
            "schema": 1,
            "workspace": "." if workspace == root else "workspace",
            "pid": os.getpid(),
            "started_at": psutil.Process().create_time(),
            "generation": generation,
            "engine": engine,
            "permission_level": permission_level,
            "reported_at": datetime.now(UTC).isoformat(),
        }
        fd, temporary = tempfile.mkstemp(prefix=".anygarden-workspace-", dir=root)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / RECEIPT_NAME)
    except (OSError, ValueError, TypeError, psutil.Error):
        pass
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
