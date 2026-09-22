"""``anygarden server migrate`` must work off the source tree (#647).

This command is the operator's only manual recovery path when the server
refuses to boot on a schema mismatch, and it was broken in two ways: it
resolved ``script_location`` relative to the current working directory,
so it raised ``Path doesn't exist: anygarden/db/migrations`` anywhere but
the repo root; and it read only ``--db``, hardcoding
``~/.anygarden/anygarden.db`` otherwise, so ``ANYGARDEN_DB_URL`` and
``--config`` were silently ignored.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from anygarden import cli
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every case outside the source tree and away from the real HOME.

    ``chdir`` is the point of the first test, and a fake ``HOME`` keeps
    ``_load_server_settings`` from picking up the developer's real
    ``~/.anygarden/config.env`` and migrating their live database.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    for name in (
        "ANYGARDEN_HOST",
        "ANYGARDEN_PORT",
        "ANYGARDEN_DB_URL",
        "ANYGARDEN_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _head_revision(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()


def test_migrate_works_outside_the_source_tree(tmp_path: Path) -> None:
    """``--db`` from an unrelated CWD migrates that database."""
    db_path = tmp_path / "explicit.db"

    result = CliRunner().invoke(
        cli.main, ["--db", f"sqlite+aiosqlite:///{db_path}", "migrate"]
    )

    assert result.exit_code == 0, result.output
    assert db_path.exists()
    assert _head_revision(db_path)
    # The old failure mode, pinned so it cannot come back.
    assert "Path doesn't exist" not in result.output


def test_migrate_honours_the_db_url_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``--db``, the app's own env var selects the target.

    Previously this was ignored in favour of a hardcoded
    ``~/.anygarden/anygarden.db``.
    """
    db_path = tmp_path / "from-env.db"
    monkeypatch.setenv("ANYGARDEN_DB_URL", f"sqlite+aiosqlite:///{db_path}")

    result = CliRunner().invoke(cli.main, ["migrate"])

    assert result.exit_code == 0, result.output
    assert db_path.exists()
    assert _head_revision(db_path)


def test_migrate_honours_the_config_file(tmp_path: Path) -> None:
    """``--config`` selects the target too — also previously ignored."""
    db_path = tmp_path / "from-config.db"
    config_path = tmp_path / "server.env"
    config_path.write_text(f"ANYGARDEN_DB_URL=sqlite+aiosqlite:///{db_path}\n")

    result = CliRunner().invoke(
        cli.main, ["--config", str(config_path), "migrate"]
    )

    assert result.exit_code == 0, result.output
    assert db_path.exists()
    assert _head_revision(db_path)


def test_migrate_names_the_database_it_touched(tmp_path: Path) -> None:
    """A bare "Migrations applied." is what let #647 hide for months."""
    db_path = tmp_path / "named.db"

    result = CliRunner().invoke(
        cli.main, ["--db", f"sqlite+aiosqlite:///{db_path}", "migrate"]
    )

    assert result.exit_code == 0, result.output
    assert str(db_path) in result.output
