"""Integrated-node schema preflight: classify, back up, prune (#920)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from anygarden.node.schema import (
    BACKUP_PREFIX,
    backup_sqlite,
    classify_revision,
    prune_backups,
    sqlite_db_path,
)

KNOWN = {"001_a", "002_b", "003_c"}


def _known(rev: str) -> bool:
    return rev in KNOWN


@pytest.mark.parametrize(
    "revisions,expected",
    [
        (None, "absent"),
        ([], "absent"),
        (["003_c"], "current"),
        (["001_a"], "behind"),
        (["999_future"], "unknown"),
        (["001_a", "002_b"], "ambiguous"),
    ],
)
def test_classify_revision(revisions, expected):
    assert classify_revision(revisions, head="003_c", is_known=_known) == expected


def test_sqlite_db_path(tmp_path):
    db = tmp_path / "anygarden.db"
    assert sqlite_db_path(f"sqlite+aiosqlite:///{db}") == db
    assert sqlite_db_path("sqlite+aiosqlite:///:memory:") is None
    assert sqlite_db_path("sqlite+aiosqlite://") is None
    assert sqlite_db_path("postgresql+asyncpg://u:p@h/db") is None


def test_backup_sqlite_is_a_consistent_copy(tmp_path):
    src = tmp_path / "anygarden.db"
    with sqlite3.connect(src) as db:
        db.execute("CREATE TABLE t (v TEXT)")
        db.execute("INSERT INTO t VALUES ('kept')")
    backup_dir = tmp_path / "backups"
    now = datetime(2026, 10, 20, 1, 2, 3, tzinfo=UTC)

    path = backup_sqlite(src, backup_dir, from_rev="001_a", now=now)

    assert path == backup_dir / f"{BACKUP_PREFIX}001_a-20261020T010203Z"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT v FROM t").fetchall() == [("kept",)]
    assert [p.name for p in backup_dir.iterdir()] == [path.name]


def test_backup_sqlite_fails_without_leaving_a_partial(tmp_path):
    src = tmp_path / "anygarden.db"
    sqlite3.connect(src).close()
    blocker = tmp_path / "backups"
    blocker.write_text("not a directory")

    with pytest.raises(OSError):
        backup_sqlite(src, blocker, from_rev="001_a", now=datetime.now(UTC))
    assert blocker.read_text() == "not a directory"


def test_prune_backups_keeps_newest(tmp_path):
    names = [
        f"{BACKUP_PREFIX}001_a-20261001T000000Z",
        f"{BACKUP_PREFIX}002_b-20261002T000000Z",
        f"{BACKUP_PREFIX}001_a-20261003T000000Z",
        f"{BACKUP_PREFIX}002_b-20261004T000000Z",
    ]
    for name in names:
        (tmp_path / name).write_text("x")
    (tmp_path / "unrelated.txt").write_text("x")

    prune_backups(tmp_path, keep=3)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == sorted([*names[1:], "unrelated.txt"])
