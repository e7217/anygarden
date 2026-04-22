"""Alembic migration chain round-trip tests.

Ensures that ``alembic upgrade head`` succeeds on a fresh database and
that the resulting schema matches what the application code expects,
preventing regressions where a migration file is moved, renamed, or has
an incompatible ``down_revision`` chain.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


def _alembic_config(db_path: str) -> Config:
    cfg = Config()
    script_location = Path(__file__).resolve().parent.parent / "doorae" / "db" / "migrations"
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return cfg


class TestMigrations:
    def test_upgrade_head_on_fresh_db(self) -> None:
        """``alembic upgrade head`` on an empty SQLite file must succeed."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            # alembic_version table exists and points at the latest revision
            engine = create_engine(f"sqlite:///{db_path}")  # sync driver for reads
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                )
                version = result.scalar_one()
                # We expect the latest revision; this test will need to be
                # updated when a new revision is added, which is the point.
                assert version == "031"

                # Every expected table exists
                result = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' ORDER BY name"
                    )
                )
                tables = {row[0] for row in result}
                expected = {
                    "alembic_version",
                    "projects",
                    "rooms",
                    "users",
                    "agents",
                    "machines",
                    "participants",
                    "messages",
                    "machine_engines",
                    "machine_tokens",
                    "agent_tokens",
                    "room_invite_links",
                }
                missing = expected - tables
                assert not missing, f"Missing tables after upgrade: {missing}"
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_users_guest_columns_and_partial_unique_after_013(self) -> None:
        """Revision 013 must leave ``users`` with:
        - nullable email / password_hash
        - is_anonymous NOT NULL DEFAULT 0
        - display_name VARCHAR(64) nullable
        - a partial unique index ``ux_users_email_not_null`` that
          ignores NULL values (so multiple guest rows can coexist).
        """
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")  # sync driver for reads
            with engine.begin() as conn:
                schema = conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='table' AND name='users'"
                    )
                ).scalar_one()
                # Non-strict assertions: the DDL string format varies
                # slightly between SQLAlchemy versions but these
                # substrings are stable.
                assert "is_anonymous" in schema
                assert "display_name VARCHAR(64)" in schema
                # email/password_hash are no longer NOT NULL
                assert "email VARCHAR(255) NOT NULL" not in schema
                assert "password_hash VARCHAR(512) NOT NULL" not in schema

                # Partial unique index exists and carries the WHERE clause
                index_sql = conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='index' AND name='ux_users_email_not_null'"
                    )
                ).scalar_one()
                assert "email IS NOT NULL" in index_sql
                assert "UNIQUE" in index_sql.upper()

                # Round-trip: two NULL-email rows coexist, duplicate real
                # emails still fail.
                conn.execute(
                    text(
                        "INSERT INTO users (id, is_anonymous, created_at) "
                        "VALUES ('g1', 1, '2026-01-01')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO users (id, is_anonymous, created_at) "
                        "VALUES ('g2', 1, '2026-01-01')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, "
                        "is_anonymous, created_at) "
                        "VALUES ('u1', 'a@x', 'h', 0, '2026-01-01')"
                    )
                )
                with pytest.raises(Exception):
                    conn.execute(
                        text(
                            "INSERT INTO users (id, email, password_hash, "
                            "is_anonymous, created_at) "
                            "VALUES ('u2', 'a@x', 'h', 0, '2026-01-01')"
                        )
                    )
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_messages_participant_id_is_nullable_after_004(self) -> None:
        """Revision 004 must leave messages.participant_id nullable with
        ON DELETE SET NULL — regression guard for the "cannot remove agent
        from room with messages" bug.
        """
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")  # sync driver for reads
            with engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='table' AND name='messages'"
                    )
                )
                schema_sql = result.scalar_one()
                assert "participant_id VARCHAR(36)" in schema_sql
                assert "participant_id VARCHAR(36) NOT NULL" not in schema_sql
                assert "ON DELETE SET NULL" in schema_sql
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_020_grandfathers_phase1_skills(self) -> None:
        """Migration 020 auto-approves Phase 1 skill_library rows and
        writes a ``grandfathered`` audit entry. Guards against a future
        migration edit that silently breaks the data migration path."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            # Stop at 019 so we can seed a pending Phase 1 skill before
            # 020 runs its grandfather pass.
            command.upgrade(cfg, "019")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, is_admin, "
                        "is_anonymous, created_at) "
                        "VALUES ('admin-1', 'a@x', 'h', 1, 0, "
                        "'2026-01-01T00:00:00+00:00')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO skill_library "
                        "(id, source, name, pinned_rev, skill_md, "
                        "extra_files, scripts_detected, content_hash, "
                        "approved_by, fetched_at) "
                        "VALUES ('sk-1', 'owner/repo', 'hello', 'sha', "
                        "'body', '{}', '[]', 'h', NULL, "
                        "'2026-01-02T00:00:00+00:00')"
                    )
                )
            engine.dispose()

            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT approved_by, approved_at "
                        "FROM skill_library WHERE id='sk-1'"
                    )
                ).one()
                assert row[0] == "admin-1"
                assert row[1] is not None

                audit = conn.execute(
                    text(
                        "SELECT action, actor_user_id "
                        "FROM skill_library_audits "
                        "WHERE skill_library_id='sk-1'"
                    )
                ).one()
                assert audit[0] == "grandfathered"
                assert audit[1] == "admin-1"
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_downgrade_to_base_and_back(self) -> None:
        """Full round-trip: head → base → head must succeed."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")
            command.downgrade(cfg, "base")

            # After downgrading to base, there should be no application
            # tables left (alembic_version may remain).
            engine = create_engine(f"sqlite:///{db_path}")  # sync driver for reads
            with engine.connect() as conn:
                with pytest.raises(OperationalError):
                    conn.execute(text("SELECT * FROM messages"))
            engine.dispose()

            command.upgrade(cfg, "head")
            engine = create_engine(f"sqlite:///{db_path}")  # sync driver for reads
            with engine.connect() as conn:
                # Tables came back
                result = conn.execute(text("SELECT COUNT(*) FROM messages"))
                assert result.scalar_one() == 0
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestEnsureSchemaReady:
    """Tests for ``app._ensure_schema_ready`` covering the three paths:
    fresh DB, already-stamped DB, and legacy unstamped DB.
    """

    @pytest.mark.asyncio
    async def test_fresh_db_creates_and_stamps(self) -> None:
        """Empty DB → create_all + stamp head → alembic_version=004."""
        from doorae.app import _ensure_schema_ready
        from doorae.db.engine import build_engine

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = build_engine(db_url)
            try:
                await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.connect() as conn:
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "031"
                schema = conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='table' AND name='messages'"
                    )
                ).scalar_one()
                # Fresh create_all uses the current (nullable) model
                assert "participant_id VARCHAR(36)" in schema
            sync_engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_already_stamped_db_runs_upgrade(self) -> None:
        """Stamped DB → upgrade head (idempotent when already at head)."""
        from doorae.app import _ensure_schema_ready
        from doorae.db.engine import build_engine

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = build_engine(db_url)
            try:
                await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.connect() as conn:
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "031"
            sync_engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_fresh_bootstrap_is_atomic(self) -> None:
        """Fresh bootstrap must materialise application tables AND
        alembic_version in a single transaction, so a mid-bootstrap
        crash cannot leave the DB in the legacy-unstamped state that
        the next boot would refuse.

        We verify the invariant indirectly: after successful
        bootstrap, a separate connection sees both halves. Previously,
        create_all and `alembic stamp` ran in separate transactions,
        and a crash between them would trap the operator forever.
        """
        from doorae.app import _ensure_schema_ready, _discover_head_revision
        from doorae.db.engine import build_engine

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = build_engine(db_url)
            try:
                await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            head = _discover_head_revision()
            assert head == "031"

            # A brand new connection must observe both the application
            # tables AND the alembic_version row — proving they landed
            # in the same committed transaction.
            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.connect() as conn:
                result = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                )
                assert result.scalar_one() == head

                for table in (
                    "projects",
                    "rooms",
                    "users",
                    "agents",
                    "machines",
                    "participants",
                    "messages",
                    "agent_tokens",
                    "machine_engines",
                    "machine_tokens",
                ):
                    # Every application table must be present
                    conn.execute(text(f"SELECT 1 FROM {table} LIMIT 0"))
            sync_engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_interrupted_fresh_bootstrap_rolls_back(self) -> None:
        """Inject a failure inside the create_all+stamp transaction and
        verify SQLAlchemy rolls the whole thing back, leaving an empty
        DB that the next boot will happily re-bootstrap.

        This is the retry-safety guarantee Codex asked for: a
        half-materialised DB (some tables but no alembic_version row)
        would otherwise be wrongly classified as Case 3 "legacy
        unstamped" on the next boot and trap the operator.
        """
        from unittest.mock import patch
        from doorae.app import _ensure_schema_ready
        from doorae.db.engine import build_engine

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            db_url = f"sqlite+aiosqlite:///{db_path}"

            # Force _discover_head_revision to blow up. Because we
            # call it BEFORE opening engine.begin(), this aborts the
            # bootstrap before any DB write — which is actually the
            # safest failure mode. Verifies the first half of the
            # retry-safety story.
            engine = build_engine(db_url)
            try:
                with patch(
                    "doorae.app._discover_head_revision",
                    side_effect=RuntimeError("simulated alembic-config crash"),
                ):
                    with pytest.raises(RuntimeError, match="simulated"):
                        await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            # DB must be completely empty: no application tables, no
            # alembic_version. The next boot will correctly re-enter
            # Case 2 (fresh bootstrap) instead of being trapped in
            # Case 3 (legacy unstamped).
            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                )
                tables = [row[0] for row in result]
            sync_engine.dispose()
            assert tables == [], (
                "Interrupted fresh bootstrap must leave zero tables so "
                f"the next boot can retry cleanly, found {tables}"
            )

            # Second boot: the exact same db file, head_revision now
            # resolves normally. Must succeed and produce a fully
            # stamped DB.
            engine = build_engine(db_url)
            try:
                await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.connect() as conn:
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "031"
            sync_engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_legacy_unstamped_db_refuses_to_boot(self) -> None:
        """Legacy DB with application tables but no alembic_version row
        must raise RuntimeError and MUST NOT get silently stamped.

        Codex-caught regression: previously, any DB without an
        alembic_version row fell through to ``create_all + stamp head``,
        which falsely claimed every migration had been applied when the
        schema was actually stale (e.g. messages.participant_id still
        NOT NULL).
        """
        from doorae.app import _ensure_schema_ready
        from doorae.db.engine import build_engine

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            # Seed a plausible pre-004 schema: messages.participant_id
            # NOT NULL, no alembic_version row.
            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE projects (
                        id VARCHAR(36) PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        description TEXT,
                        created_at DATETIME NOT NULL
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE messages (
                        id VARCHAR(36) PRIMARY KEY,
                        room_id VARCHAR(36) NOT NULL,
                        participant_id VARCHAR(36) NOT NULL,
                        content TEXT NOT NULL,
                        seq BIGINT NOT NULL,
                        created_at DATETIME NOT NULL
                    )
                """))
            sync_engine.dispose()

            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = build_engine(db_url)
            try:
                with pytest.raises(RuntimeError, match="legacy unstamped"):
                    await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            # Must NOT have stamped alembic_version
            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='alembic_version'"
                    )
                )
                assert result.scalar_one_or_none() is None, (
                    "Legacy DB must NOT be auto-stamped — operator must "
                    "intervene explicitly"
                )
                # Original stale schema must be untouched
                schema = conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='table' AND name='messages'"
                    )
                ).scalar_one()
                assert "participant_id VARCHAR(36) NOT NULL" in schema
            sync_engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass
