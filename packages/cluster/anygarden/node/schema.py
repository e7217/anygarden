"""Schema preflight for the integrated node: back up before upgrading (#920).

``anygarden start`` used to refuse any database whose revision was not head,
so every package upgrade needed a manual stop/backup/migrate/start. A known
older revision is now snapshotted next to the data directory and upgraded on
boot; a revision this code does not know (a newer build wrote it) is still
refused, because there is no script to bring it back.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy.engine import make_url

BACKUP_PREFIX = "anygarden.db.bak-"
BACKUP_KEEP = 3

RevisionState = Literal["absent", "current", "behind", "unknown", "ambiguous"]


def classify_revision(
    revisions: list[str] | None, *, head: str, is_known: Callable[[str], bool]
) -> RevisionState:
    """Place the stamped revision relative to this build's migration chain."""
    if not revisions:
        # Fresh or legacy-unstamped: _ensure_schema_ready decides.
        return "absent"
    if len(revisions) != 1:
        return "ambiguous"
    (revision,) = revisions
    if revision == head:
        return "current"
    return "behind" if is_known(revision) else "unknown"


def sqlite_db_path(db_url: str) -> Path | None:
    """The file behind a SQLite URL; ``None`` for memory or other dialects."""
    url = make_url(db_url)
    if not url.drivername.startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def backup_sqlite(
    db_path: Path, backup_dir: Path, *, from_rev: str, now: datetime
) -> Path:
    """Write a consistent snapshot of ``db_path`` and return its path.

    Uses SQLite's online backup API rather than a file copy so the snapshot
    is page-consistent regardless of journal mode or open connections. The
    copy is written under a temporary name first, so a half-written file is
    never mistaken for a usable backup.
    """
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    target = backup_dir / f"{BACKUP_PREFIX}{from_rev}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    partial = target.with_name(f".{target.name}.partial")
    try:
        source = sqlite3.connect(db_path)
        try:
            dest = sqlite3.connect(partial)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        if os.name != "nt":
            os.chmod(partial, 0o600)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def prune_backups(backup_dir: Path, *, keep: int = BACKUP_KEEP) -> None:
    """Keep the ``keep`` newest backups (by the timestamp in their name)."""
    backups = sorted(
        (p for p in backup_dir.glob(f"{BACKUP_PREFIX}*") if p.is_file()),
        key=lambda p: p.name.rsplit("-", 1)[-1],
    )
    for stale in backups[:-keep] if keep > 0 else backups:
        stale.unlink(missing_ok=True)
