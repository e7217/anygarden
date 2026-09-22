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
    script_location = Path(__file__).resolve().parent.parent / "anygarden" / "db" / "migrations"
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
                assert version == "075_direct_endpoints"

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
                    "room_authorization_audits",
                    "thread_participant_states",
                    "agent_turns",
                    "agent_turn_attempts",
                    "agent_turn_outbox",
                    "workspace_attachments",
                    "workspace_invocation_audits",
                }
                missing = expected - tables
                assert not missing, f"Missing tables after upgrade: {missing}"

                participant_columns = {
                    row[1]: row[2]
                    for row in conn.execute(text("PRAGMA table_info(participants)"))
                }
                assert participant_columns["last_read_message_seq"].upper() == "BIGINT"

                room_columns = {
                    row[1]: row
                    for row in conn.execute(text("PRAGMA table_info(rooms)"))
                }
                assert room_columns["visibility"][3] == 1  # NOT NULL
                assert room_columns["visibility"][4].strip("'") == "private"
                assert "archived_at" in room_columns
                assert "archived_by" in room_columns

                indexes = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type='index'"
                        )
                    )
                }
                assert "ix_rooms_visibility_archived" in indexes
                assert "ix_participants_room_role" in indexes
                assert "ix_room_authorization_audits_actor_at" in indexes
                assert "ix_room_authorization_audits_room_at" in indexes
                assert "ix_room_authorization_audits_scope_at" in indexes
                assert "ix_messages_room_root_seq" in indexes
                assert "ix_thread_participant_states_root_read" in indexes
                assert "ix_agents_lifecycle_delivery_lease" in indexes

                fts_triggers = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type='trigger' AND name LIKE 'messages_fts_%'"
                        )
                    )
                }
                assert fts_triggers == {
                    "messages_fts_insert",
                    "messages_fts_delete",
                    "messages_fts_update",
                }

                message_columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(messages)"))
                }
                assert {"parent_message_id", "root_message_id"} <= message_columns

                agent_columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(agents)"))
                }
                assert {
                    "lifecycle_lease_token",
                    "lifecycle_lease_expires_at",
                    "lifecycle_delivery_state",
                    "legacy_report_generation",
                } <= agent_columns

                # Authorization audits are append-only historical evidence.
                # Actor and room deletion must never cascade into this table.
                audit_foreign_keys = list(
                    conn.execute(
                        text("PRAGMA foreign_key_list(room_authorization_audits)")
                    )
                )
                assert audit_foreign_keys == []
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_machine_system_info_columns_after_053(self) -> None:
        """Revision 053 (#523) adds machines.description / lan_ip /
        os_platform, and downgrade 053 → 052 removes them."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")
            new_cols = {"description", "lan_ip", "os_platform"}
            with engine.connect() as conn:
                cols = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(machines)"))
                }
                assert new_cols <= cols, f"Missing after head: {new_cols - cols}"

            # Downgrade one step and confirm the new columns are gone.
            command.downgrade(cfg, "052")
            with engine.connect() as conn:
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "052"
                cols = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(machines)"))
                }
                assert not (new_cols & cols), f"Leftover after downgrade: {new_cols & cols}"
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

    def test_038_backfills_assigned_at_for_assigned_tasks(self) -> None:
        """Migration 038 backfills ``tasks.assigned_at`` from
        ``created_at`` for rows with an assignee, and leaves
        unassigned rows NULL. Guards against a future edit that
        silently breaks #314 sweeper's NULL-skip semantics."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            # Stop at 037 to seed ``tasks`` rows before 038 backfills.
            command.upgrade(cfg, "037")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.begin() as conn:
                # FK targets: a user, a project, a room, a participant.
                conn.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, is_admin, "
                        "is_anonymous, created_at) VALUES "
                        "('u-1', 'u@x', 'h', 0, 0, '2026-01-01T00:00:00+00:00')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO projects (id, name, created_at) "
                        "VALUES ('p-1', 'P', '2026-01-01T00:00:00+00:00')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO rooms (id, project_id, name, created_at, is_dm, "
                        "context_window_enabled, speaker_strategy, "
                        "current_speaker_index, ephemeral, allow_human_assignment) "
                        "VALUES ('r-1', 'p-1', 'R', '2026-01-01T00:00:00+00:00', "
                        "0, 0, 'mentioned_only', 0, 0, 0)"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO participants (id, room_id, user_id, role, "
                        "joined_at) VALUES "
                        "('part-1', 'r-1', 'u-1', 'member', "
                        "'2026-01-01T00:00:00+00:00')"
                    )
                )
                # Two tasks: one assigned, one not.
                conn.execute(
                    text(
                        "INSERT INTO tasks (id, room_id, title, status, "
                        "assignee_participant_id, created_by, created_at, "
                        "triggered_by, is_interesting) VALUES "
                        "('t-assigned', 'r-1', 'A', 'todo', 'part-1', 'u-1', "
                        "'2026-01-02T03:04:05+00:00', 'manual', 0)"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO tasks (id, room_id, title, status, "
                        "assignee_participant_id, created_by, created_at, "
                        "triggered_by, is_interesting) VALUES "
                        "('t-unassigned', 'r-1', 'B', 'todo', NULL, 'u-1', "
                        "'2026-01-02T03:04:05+00:00', 'manual', 0)"
                    )
                )
            engine.dispose()

            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                row_a = conn.execute(
                    text(
                        "SELECT assigned_at, created_at FROM tasks "
                        "WHERE id='t-assigned'"
                    )
                ).one()
                # Backfilled to created_at (string compare on ISO is fine).
                assert row_a[0] == row_a[1]
                row_b = conn.execute(
                    text(
                        "SELECT assigned_at FROM tasks WHERE id='t-unassigned'"
                    )
                ).one()
                assert row_b[0] is None
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_047_cost_usd_column_up_and_down(self) -> None:
        """#461 (Wave 2d) — migration 047 adds ``usage_ledger.cost_usd``
        on upgrade head and removes it on downgrade 047 → 046."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.begin() as conn:
                cols = {
                    row[1]
                    for row in conn.execute(
                        text("PRAGMA table_info(usage_ledger)")
                    )
                }
                # A nonexistent table yields an empty PRAGMA — assert the
                # table exists before judging columns.
                assert cols, "usage_ledger table missing at head"
                assert "cost_usd" in cols
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                # The cost_usd column added by 047 remains through head.
                assert version == "075_direct_endpoints"
                # Seed a real row so the downgrade is verified against
                # actual data, not an empty table (task #98 review).
                conn.execute(
                    text(
                        "INSERT INTO usage_ledger "
                        "(id, timestamp, identity_kind, identity_id, "
                        "model_name, status_code) VALUES "
                        "('seed-1', '2026-09-22 00:00:00.000000', 'agent', "
                        "'agent-1', 'glm-5.3-flash', 200)"
                    )
                )
            engine.dispose()

            # Downgrade two steps (head 048 → 047 → 046) and confirm the
            # column is gone and the head moved back. At 046 the table is
            # still named llm_gateway_usage (the 074 rename happens later),
            # so query THAT name here.
            command.downgrade(cfg, "046")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                table = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name IN ('usage_ledger', 'llm_gateway_usage')"
                    )
                ).scalar()
                assert table == "llm_gateway_usage", (
                    "046 must keep the pre-rename table name"
                )
                cols = {
                    row[1]
                    for row in conn.execute(
                        text("PRAGMA table_info(llm_gateway_usage)")
                    )
                }
                assert cols, "llm_gateway_usage table missing at 046"
                assert "cost_usd" not in cols
                # The seeded row must survive the downgrade.
                count = conn.execute(
                    text("SELECT COUNT(*) FROM llm_gateway_usage")
                ).scalar_one()
                assert count == 1, f"seeded row lost on downgrade: {count}"
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "046"
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_048_agent_turn_tasks_up_and_down(self) -> None:
        """#463 (Wave 2) — migration 048 creates ``agent_turn_tasks`` on
        upgrade head and drops it on downgrade 048 → 047."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    )
                }
                assert "agent_turn_tasks" in tables
                cols = {
                    row[1]: row
                    for row in conn.execute(
                        text("PRAGMA table_info(agent_turn_tasks)")
                    )
                }
                # PK on request_id, the required columns are present.
                assert cols["request_id"][5] == 1  # pk position 1
                assert "task_id" in cols
                assert "redispatch_count" in cols
                assert "created_at" in cols
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "075_direct_endpoints"
            engine.dispose()

            # Downgrade to 047: ``agent_turn_tasks`` (added by 048) is gone
            # and the head moved back.
            command.downgrade(cfg, "047")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    )
                }
                assert "agent_turn_tasks" not in tables
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "047"
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_049_agent_turn_timeout_up_and_down(self) -> None:
        """#493 — migration 049 adds ``agents.turn_timeout_sec`` on upgrade
        head and drops it on downgrade 049 → 048."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                cols = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(agents)"))
                }
                assert "turn_timeout_sec" in cols
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "075_direct_endpoints"
            engine.dispose()

            # Downgrade one step (049 → 048): the column is gone and the
            # head moved back.
            command.downgrade(cfg, "048")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                cols = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(agents)"))
                }
                assert "turn_timeout_sec" not in cols
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "048"
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_060_message_linked_tasks_up_and_down(self) -> None:
        """060 adds the nullable source FK plus unique lookup indexes."""

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                columns = {
                    row[1]: row for row in conn.execute(text("PRAGMA table_info(tasks)"))
                }
                assert columns["source_message_id"][3] == 0  # nullable
                foreign_keys = list(
                    conn.execute(text("PRAGMA foreign_key_list(tasks)"))
                )
                assert any(
                    row[2] == "messages"
                    and row[3] == "source_message_id"
                    and row[4] == "id"
                    and row[6] == "SET NULL"
                    for row in foreign_keys
                )
                indexes = {
                    row[1]: row for row in conn.execute(text("PRAGMA index_list(tasks)"))
                }
                assert "ix_tasks_source_message_id" in indexes
                unique_columns = {
                    tuple(
                        info[2]
                        for info in conn.execute(
                            text(f'PRAGMA index_info("{name}")')
                        )
                    )
                    for name, row in indexes.items()
                    if row[2] == 1
                }
                assert ("source_message_id",) in unique_columns
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "075_direct_endpoints"
            engine.dispose()

            command.downgrade(cfg, "059")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                columns = {
                    row[1] for row in conn.execute(text("PRAGMA table_info(tasks)"))
                }
                assert "source_message_id" not in columns
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_061_durable_turns_up_and_down(self) -> None:
        """061 adds turn recovery tables and agent drain state."""

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                agent_columns = {
                    row[1] for row in conn.execute(text("PRAGMA table_info(agents)"))
                }
                assert {
                    "pending_generation",
                    "restart_requested_at",
                    "restart_deadline_at",
                    "manifest_hash",
                    "pending_manifest_hash",
                } <= agent_columns
                tables = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                }
                assert {
                    "agent_turns",
                    "agent_turn_attempts",
                    "agent_turn_outbox",
                } <= tables
                indexes = {
                    row[1]
                    for row in conn.execute(
                        text("PRAGMA index_list(agent_turn_attempts)")
                    )
                }
                assert "uq_agent_turn_attempt_active" in indexes
            engine.dispose()

            command.downgrade(cfg, "060")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                }
                assert "agent_turns" not in tables
                agent_columns = {
                    row[1] for row in conn.execute(text("PRAGMA table_info(agents)"))
                }
                assert "pending_generation" not in agent_columns
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "060"
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_062_workspace_attachments_up_and_down(self) -> None:
        """062 adds opaque attachment/audit state and turn epoch binding."""

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                }
                assert {
                    "workspace_attachments",
                    "workspace_invocation_audits",
                } <= tables
                machine_columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(machines)"))
                }
                assert {
                    "control_capabilities",
                    "workspace_catalog",
                    "workspace_signing_public_key",
                } <= machine_columns
                audit_foreign_keys = list(
                    conn.execute(
                        text("PRAGMA foreign_key_list(workspace_invocation_audits)")
                    )
                )
                assert audit_foreign_keys == []
                turn_columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(agent_turns)"))
                }
                assert {
                    "workspace_attachment_id",
                    "workspace_attachment_epoch",
                } <= turn_columns
            engine.dispose()

            command.downgrade(cfg, "061")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                }
                assert "workspace_attachments" not in tables
                machine_columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(machines)"))
                }
                assert "workspace_catalog" not in machine_columns
                assert "workspace_signing_public_key" not in machine_columns
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "061"
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_063_lifecycle_dispatch_lease_up_and_down(self) -> None:
        """063 adds durable dispatch ownership and a legacy report epoch."""

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "062")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO agents "
                        "(id, name, engine, placed_on_machine_id, generation, "
                        "created_at) "
                        "VALUES ('placed-agent', 'placed', 'echo', "
                        "'machine-old', 9, CURRENT_TIMESTAMP)"
                    )
                )
            engine.dispose()

            command.upgrade(cfg, "head")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "075_direct_endpoints"
                agent_columns = {
                    row[1] for row in conn.execute(text("PRAGMA table_info(agents)"))
                }
                assert {
                    "lifecycle_lease_token",
                    "lifecycle_lease_expires_at",
                    "lifecycle_delivery_state",
                    "legacy_report_generation",
                } <= agent_columns
                legacy_epoch = conn.execute(
                    text(
                        "SELECT legacy_report_generation FROM agents "
                        "WHERE id = 'placed-agent'"
                    )
                ).scalar_one()
                assert legacy_epoch == 9
                indexes = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='index'")
                    )
                }
                assert "ix_agents_lifecycle_delivery_lease" in indexes
            engine.dispose()

            command.downgrade(cfg, "062")
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert version == "062"
                agent_columns = {
                    row[1] for row in conn.execute(text("PRAGMA table_info(agents)"))
                }
                assert "lifecycle_lease_token" not in agent_columns
                assert "legacy_report_generation" not in agent_columns
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
            with engine.connect() as conn, pytest.raises(OperationalError):
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
        from anygarden.app import _ensure_schema_ready
        from anygarden.db.engine import build_engine

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
                assert version == "075_direct_endpoints"
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
        from anygarden.app import _ensure_schema_ready
        from anygarden.db.engine import build_engine

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
                assert version == "075_direct_endpoints"
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
        from anygarden.app import _discover_head_revision, _ensure_schema_ready
        from anygarden.db.engine import build_engine

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
            assert head == "075_direct_endpoints"

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

        from anygarden.app import _ensure_schema_ready
        from anygarden.db.engine import build_engine

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
                    "anygarden.app._discover_head_revision",
                    side_effect=RuntimeError("simulated alembic-config crash"),
                ), pytest.raises(RuntimeError, match="simulated"):
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
                assert version == "075_direct_endpoints"
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
        from anygarden.app import _ensure_schema_ready
        from anygarden.db.engine import build_engine

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


class TestUpgradeFailureRecovery:
    """Tests for the Case 1 recovery path in ``app._ensure_schema_ready``.

    SQLite reparses the *entire* schema on ``ALTER TABLE ... RENAME``, so a
    single dangling trigger — ``messages_fts_*`` left behind when the FTS
    virtual table went missing (#520) — breaks Alembic batch migrations on
    completely unrelated tables. That is what made ``_self_heal_message_fts``
    unreachable: it ran *after* the upgrade that its own target state killed
    (#646).

    Worse, a failed upgrade is not rolled back. pysqlite only opens an
    implicit transaction before DML, so the ``CREATE TABLE _alembic_tmp_*``
    that opens a batch migration is committed in autocommit and survives the
    failure. Repairing only the trigger therefore still fails on the retry
    with "table _alembic_tmp_tasks already exists".

    Both leftovers must be cleaned up for the retry to succeed. Revision 059
    is the last one before ``060_message_linked_tasks``, which does
    ``batch_alter_table("tasks")`` — the exact migration reported in #646.
    """

    @staticmethod
    def _upgrade_to_059(db_path: str) -> None:
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "059")

    @staticmethod
    def _table_names(db_path: str) -> set[str]:
        sync_engine = create_engine(f"sqlite:///{db_path}")
        try:
            with sync_engine.connect() as conn:
                return {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    ).all()
                }
        finally:
            sync_engine.dispose()

    @staticmethod
    def _head() -> str:
        from anygarden.app import _discover_head_revision

        return _discover_head_revision()

    @staticmethod
    def _revision(db_path: str) -> str:
        sync_engine = create_engine(f"sqlite:///{db_path}")
        try:
            with sync_engine.connect() as conn:
                return conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
        finally:
            sync_engine.dispose()

    @pytest.mark.asyncio
    async def test_recovers_from_dangling_fts_trigger(self) -> None:
        """messages_fts dropped, its triggers left behind → boot must still
        reach head instead of dying inside an unrelated batch migration."""
        from anygarden.app import _ensure_schema_ready
        from anygarden.db.engine import build_engine

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            self._upgrade_to_059(db_path)

            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.begin() as conn:
                # Drop the virtual table only; SQLite keeps the triggers.
                conn.execute(text("DROP TABLE messages_fts"))
                triggers = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='trigger'")
                    ).all()
                }
            sync_engine.dispose()
            assert "messages_fts_insert" in triggers, (
                "test setup is wrong: the dangling trigger must survive the drop"
            )

            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = build_engine(db_url)
            try:
                await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            assert self._revision(db_path) == self._head()
            assert "messages_fts" in self._table_names(db_path), (
                "the self-heal must also have restored the FTS index"
            )
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_recovers_from_leftover_alembic_tmp_table(self) -> None:
        """A ``_alembic_tmp_*`` table left by a previously failed boot must
        not block the retry. Batch mode issues a bare CREATE TABLE, so the
        leftover collides on the next attempt."""
        from anygarden.app import _ensure_schema_ready
        from anygarden.db.engine import build_engine

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            self._upgrade_to_059(db_path)

            sync_engine = create_engine(f"sqlite:///{db_path}")
            with sync_engine.begin() as conn:
                conn.execute(text("CREATE TABLE _alembic_tmp_tasks (id VARCHAR(36))"))
            sync_engine.dispose()

            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = build_engine(db_url)
            try:
                await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            assert self._revision(db_path) == self._head()
            assert "_alembic_tmp_tasks" not in self._table_names(db_path), (
                "the stale batch-migration scratch table must be cleaned up"
            )
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_unrelated_upgrade_failure_is_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure the repair step cannot explain must propagate unchanged,
        and must not be retried — otherwise a genuine schema problem hides
        behind a second identical traceback."""
        import anygarden.app as app_module
        from anygarden.app import _ensure_schema_ready
        from anygarden.db.engine import build_engine

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            calls: list[tuple[str, str]] = []

            async def _boom(action: str, url: str, target: str) -> None:
                calls.append((action, target))
                raise RuntimeError("unrelated migration explosion")

            monkeypatch.setattr(app_module, "_alembic_action", _boom)

            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = build_engine(db_url)
            try:
                with pytest.raises(RuntimeError, match="unrelated migration explosion"):
                    await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            assert len(calls) == 1, (
                f"a repair-less failure must not be retried, got {calls}"
            )
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestMigrationFailureMessage:
    """Tests for ``app._migration_failure_message`` (#650).

    A failed ``alembic upgrade head`` used to surface as a bare ~80-line
    SQLAlchemy traceback: no database, no revisions, no recovery hint. The
    sibling paths already explain themselves — Case 3 prints a numbered
    baseline procedure, and the integrated-node guard names what it needs —
    so Case 1 was the odd one out.
    """

    def test_password_is_never_rendered(self) -> None:
        """The message is the single most-copied artefact of a failed boot
        (logs, issue reports, screenshots). A DSN password in it cannot be
        recalled, so masking is a hard requirement, not a nicety."""
        from anygarden.app import _migration_failure_message

        msg = _migration_failure_message(
            db_url="postgresql+asyncpg://ag:sup3rs3cret@db.internal:5432/anygarden",
            current_rev="059",
            head_rev="074_usage_ledger",
            cause=RuntimeError("boom"),
        )
        assert "sup3rs3cret" not in msg
        assert "db.internal" in msg, "the host must still be identifiable"

    def test_sqlite_path_is_visible(self) -> None:
        from anygarden.app import _migration_failure_message

        msg = _migration_failure_message(
            db_url="sqlite+aiosqlite:////home/u/.anygarden/anygarden.db",
            current_rev="059",
            head_rev="074_usage_ledger",
            cause=RuntimeError("boom"),
        )
        assert "/home/u/.anygarden/anygarden.db" in msg
        assert "059" in msg
        assert "074_usage_ledger" in msg

    def test_reports_innermost_cause(self) -> None:
        """SQLAlchemy wraps the DBAPI error; the actionable line is the one
        underneath. Reporting the wrapper reproduces the original complaint."""
        from anygarden.app import _migration_failure_message

        try:
            try:
                raise ValueError("no such table: main.messages_fts")
            except ValueError as inner:
                raise RuntimeError("(sqlite3.OperationalError) wrapped") from inner
        except RuntimeError as outer:
            msg = _migration_failure_message(
                db_url="sqlite+aiosqlite:///x.db",
                current_rev="059",
                head_rev="072",
                cause=outer,
            )
        assert "no such table: main.messages_fts" in msg

    def test_repair_summary_only_when_a_repair_happened(self) -> None:
        """After a repaired-then-still-failed upgrade the operator must be
        told what was already changed, or they will misjudge the DB state."""
        from anygarden.app import _migration_failure_message

        without = _migration_failure_message(
            db_url="sqlite+aiosqlite:///x.db",
            current_rev="059",
            head_rev="072",
            cause=RuntimeError("boom"),
        )
        with_repair = _migration_failure_message(
            db_url="sqlite+aiosqlite:///x.db",
            current_rev="059",
            head_rev="072",
            cause=RuntimeError("boom"),
            repaired={
                "dropped_tmp_tables": ["_alembic_tmp_tasks"],
                "fts_recreated": True,
            },
        )
        assert "_alembic_tmp_tasks" not in without
        assert "_alembic_tmp_tasks" in with_repair
        assert "messages_fts" in with_repair

    def test_unknown_revision_does_not_break_the_message(self) -> None:
        """Building the diagnostic must never be the thing that fails."""
        from anygarden.app import _migration_failure_message

        msg = _migration_failure_message(
            db_url="sqlite+aiosqlite:///x.db",
            current_rev=None,
            head_rev=None,
            cause=RuntimeError("boom"),
        )
        assert "unknown" in msg
        assert "boom" in msg

    def test_unparseable_url_does_not_break_the_message(self) -> None:
        from anygarden.app import _migration_failure_message

        msg = _migration_failure_message(
            db_url="::: not a url :::",
            current_rev="059",
            head_rev="072",
            cause=RuntimeError("boom"),
        )
        assert "boom" in msg
        assert "not a url" not in msg, (
            "an unparseable URL must be elided rather than echoed verbatim"
        )


class TestUpgradeFailureIsReportedWithContext:
    """End-to-end: an unrecoverable upgrade failure must arrive as a
    ``RuntimeError`` carrying database, revisions and root cause (#650)."""

    @pytest.mark.asyncio
    async def test_unrecoverable_failure_carries_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import anygarden.app as app_module
        from anygarden.app import _ensure_schema_ready
        from anygarden.db.engine import build_engine

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "059")

            async def _boom(action: str, url: str, target: str) -> None:
                raise RuntimeError("unrelated migration explosion")

            monkeypatch.setattr(app_module, "_alembic_action", _boom)

            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = build_engine(db_url)
            try:
                with pytest.raises(RuntimeError) as excinfo:
                    await _ensure_schema_ready(engine, db_url)
            finally:
                await engine.dispose()

            msg = str(excinfo.value)
            assert "Schema migration failed" in msg
            assert db_path in msg
            assert "059" in msg
            assert "unrelated migration explosion" in msg
            assert excinfo.value.__cause__ is not None, (
                "the original exception must stay chained for the traceback"
            )
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


def test_073_provider_preserves_existing_agents(tmp_path):
    db_path = str(tmp_path / "provider.db")
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "072_drop_agent_collaboration_mode")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO agents (id, name, engine, generation, created_at) VALUES ('legacy', 'Legacy Pi', 'pi-cli', 9, CURRENT_TIMESTAMP)"))
    command.upgrade(cfg, "073_agent_provider")
    with engine.connect() as conn:
        row = conn.execute(text("SELECT name, engine, generation, provider FROM agents WHERE id='legacy'")).one()
        assert tuple(row) == ("Legacy Pi", "pi-cli", 9, None)
    command.downgrade(cfg, "072_drop_agent_collaboration_mode")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name, generation FROM agents WHERE id='legacy'")).one() == ("Legacy Pi", 9)
    engine.dispose()


def test_075_preserves_agents_and_usage_across_upgrade_and_downgrade(tmp_path):
    from datetime import datetime, timezone
    from sqlalchemy import inspect

    path = str(tmp_path / "endpoint.db")
    cfg = _alembic_config(path)
    command.upgrade(cfg, "074_usage_ledger")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agents (id, name, engine, provider, generation, created_at) VALUES ('a', 'Local', 'pi-cli', 'custom', 12, CURRENT_TIMESTAMP)"
            )
        )
        # Populate an existing ledger row with all required columns generically,
        # using the same ORM defaults as normal writes.
        from anygarden.db.models import UsageLedger

        conn.execute(
            UsageLedger.__table__.insert().values(
                id="usage-existing",
                model_name="m",
                timestamp=datetime.now(timezone.utc),
                agent_id="a",
                identity_kind="agent",
                identity_id="a",
                status_code=200,
            )
        )
    command.upgrade(cfg, "075_direct_endpoints")
    with engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT provider, generation, base_url, credential_ref FROM agents WHERE id='a'"
            )
        ).one() == ("custom", 12, None, None)
        assert (
            conn.execute(
                text("SELECT count(*) FROM usage_ledger WHERE id='usage-existing'")
            ).scalar_one()
            == 1
        )
        assert "engine_credentials" in inspect(conn).get_table_names()
    command.downgrade(cfg, "074_usage_ledger")
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT provider, generation FROM agents WHERE id='a'")
        ).one() == ("custom", 12)
        assert (
            conn.execute(
                text("SELECT count(*) FROM usage_ledger WHERE id='usage-existing'")
            ).scalar_one()
            == 1
        )
        assert "engine_credentials" not in inspect(conn).get_table_names()
    engine.dispose()
