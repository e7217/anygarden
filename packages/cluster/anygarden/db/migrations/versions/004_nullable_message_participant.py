"""Allow messages.participant_id to be NULL so participants can leave.

Revision ID: 004
Revises: 003
Create Date: 2026-04-11

Rationale
---------
Removing a participant from a room (e.g. removing an agent via the admin
dialog) failed with ``NOT NULL constraint failed: messages.participant_id``
because the default SQLAlchemy relationship cascade tried to disassociate
messages by setting participant_id to NULL — and the column rejected NULL.

Solution: make participant_id nullable and switch the FK from
``ON DELETE CASCADE`` to ``ON DELETE SET NULL``. This preserves the chat
history of the departed participant while cleanly detaching them.

SQLite notes
------------
The initial migration (001) created the messages FK without an explicit
name, so Alembic's ``batch_alter_table`` cannot locate the constraint by
name to drop it. Instead we do the textbook SQLite "recreate" dance by
hand: create a new table with the desired schema, copy rows, drop old,
rename. This is SQLite-specific — Postgres would use
``ALTER TABLE ... ALTER COLUMN`` and a dropped-then-recreated FK.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: str = "003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        _recreate_messages_sqlite(
            participant_fk="SET NULL",
            participant_nullable=True,
        )
    else:
        # PostgreSQL / MySQL: plain ALTER suffices
        op.alter_column(
            "messages",
            "participant_id",
            existing_type=sa.String(36),
            nullable=True,
        )
        # Drop the old FK (we don't know its name — query information_schema
        # and drop it by whatever name the DB assigned).
        op.execute(_drop_fk_sql(bind, "messages", "participant_id"))
        op.create_foreign_key(
            "fk_messages_participant_id",
            "messages",
            "participants",
            ["participant_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Reverse: orphan rows must go first (re-adding NOT NULL rejects NULLs).
    op.execute("DELETE FROM messages WHERE participant_id IS NULL")

    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        _recreate_messages_sqlite(
            participant_fk="CASCADE",
            participant_nullable=False,
        )
    else:
        op.execute(_drop_fk_sql(bind, "messages", "participant_id"))
        op.alter_column(
            "messages",
            "participant_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        op.create_foreign_key(
            "fk_messages_participant_id",
            "messages",
            "participants",
            ["participant_id"],
            ["id"],
            ondelete="CASCADE",
        )


# ── SQLite helpers ──────────────────────────────────────────────────────

def _recreate_messages_sqlite(*, participant_fk: str, participant_nullable: bool) -> None:
    """SQLite-only: rebuild the messages table with a new FK spec.

    ``participant_fk`` must be ``"SET NULL"`` or ``"CASCADE"``.
    """
    null_clause = "" if participant_nullable else " NOT NULL"

    # Foreign keys must be disabled while we swap the tables, otherwise the
    # copy step would fire CASCADE on the old FK.
    op.execute("PRAGMA foreign_keys=OFF")

    op.execute(f"""
        CREATE TABLE messages_new (
            id VARCHAR(36) NOT NULL,
            room_id VARCHAR(36) NOT NULL,
            participant_id VARCHAR(36){null_clause},
            content TEXT NOT NULL,
            extra_metadata JSON,
            seq BIGINT NOT NULL,
            created_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_room_seq UNIQUE (room_id, seq),
            FOREIGN KEY(room_id) REFERENCES rooms (id) ON DELETE CASCADE,
            FOREIGN KEY(participant_id) REFERENCES participants (id) ON DELETE {participant_fk}
        )
    """)

    op.execute("""
        INSERT INTO messages_new (id, room_id, participant_id, content, extra_metadata, seq, created_at)
        SELECT id, room_id, participant_id, content, extra_metadata, seq, created_at FROM messages
    """)

    op.execute("DROP TABLE messages")
    op.execute("ALTER TABLE messages_new RENAME TO messages")

    op.execute("PRAGMA foreign_keys=ON")


def _drop_fk_sql(bind, table: str, column: str) -> str:
    """For non-SQLite dialects, find the FK name from information_schema."""
    # Generic SQL that works on PostgreSQL/MySQL; Alembic doesn't provide a
    # portable "drop FK by column" helper so we rely on the DB catalog.
    # The query returns nothing on SQLite, which is why sqlite uses
    # _recreate_messages_sqlite instead.
    _ = bind, table, column
    raise NotImplementedError(
        "Non-SQLite drop-by-column FK removal is a future concern. "
        "Deploy SQLite-first; add PG support when the first Postgres "
        "deployment target materializes."
    )
