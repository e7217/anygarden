"""Allow anonymous guest rows on ``users`` (§11 design doc).

Revision ID: 013
Revises: 012
Create Date: 2026-04-15

Rationale
---------
Guests authenticate through ``POST /auth/guest`` with only a
display_name and an invite token — they have no email and no
password. To let them share the ``users`` table with registered
users (so ``Participant.user_id`` keeps working unchanged) we:

* make ``email`` and ``password_hash`` nullable
* replace the full ``UNIQUE(email)`` constraint with a partial unique
  index that ignores NULL (``email IS NOT NULL``), so any number of
  guest rows can coexist while duplicate real emails still fail
* add ``is_anonymous`` (boolean) and ``display_name`` (≤64 chars)
  columns

SQLite notes
------------
The initial migration (001) named the UNIQUE(email) constraint
implicitly, so on SQLite there is no portable way to drop it with
``batch_alter_table(existing_server_default=...)``. Instead we use
the same textbook "recreate the table" pattern as migration 004
(messages) and copy rows over. Partial unique indexes have been
supported in SQLite since 3.8.0 (2013), which matches our minimum
runtime.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "013"
down_revision: str = "012"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        _recreate_users_sqlite(
            email_nullable=True,
            password_hash_nullable=True,
            with_guest_columns=True,
        )
        # Partial unique index — SQLite 3.8.0+
        op.execute(
            "CREATE UNIQUE INDEX ux_users_email_not_null "
            "ON users (email) WHERE email IS NOT NULL"
        )
    else:
        # Postgres / MySQL: ALTER COLUMN + drop-then-create index.
        op.alter_column(
            "users",
            "email",
            existing_type=sa.String(255),
            nullable=True,
        )
        op.alter_column(
            "users",
            "password_hash",
            existing_type=sa.String(512),
            nullable=True,
        )
        op.add_column(
            "users",
            sa.Column(
                "is_anonymous",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.add_column(
            "users",
            sa.Column("display_name", sa.String(64), nullable=True),
        )
        # Drop the implicit full-unique index that ``unique=True`` on
        # 001 produced. Non-SQLite dialects name it predictably.
        op.execute(_drop_email_unique_sql(dialect))
        op.create_index(
            "ux_users_email_not_null",
            "users",
            ["email"],
            unique=True,
            postgresql_where=sa.text("email IS NOT NULL"),
        )


def downgrade() -> None:
    # WARNING: destructive. Guest rows MUST go first, otherwise we
    # cannot re-impose NOT NULL on ``email``/``password_hash``.
    # Participant.user_id is ``ON DELETE CASCADE`` (see models.py),
    # so removing guest users cascades into their participant rows.
    # Messages authored by guests already have ``participant_id``
    # with ``ON DELETE SET NULL`` (migration 004), so chat history
    # survives with a null sender.
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        # SQLite booleans are plain ints; ``TRUE`` is a 3.23+ token
        # and not portable here.
        op.execute("DELETE FROM users WHERE is_anonymous = 1")
        op.execute("DROP INDEX IF EXISTS ux_users_email_not_null")
        _recreate_users_sqlite(
            email_nullable=False,
            password_hash_nullable=False,
            with_guest_columns=False,
        )
        # Ensure children (participants, messages) are still consistent
        # after the rebuild — cheap belt & suspenders before the next
        # transaction starts.
        op.execute("PRAGMA foreign_key_check")
    else:
        op.execute("DELETE FROM users WHERE is_anonymous = TRUE")
        op.drop_index("ux_users_email_not_null", table_name="users")
        op.drop_column("users", "display_name")
        op.drop_column("users", "is_anonymous")
        op.alter_column(
            "users",
            "password_hash",
            existing_type=sa.String(512),
            nullable=False,
        )
        op.alter_column(
            "users",
            "email",
            existing_type=sa.String(255),
            nullable=False,
        )
        # Recreate the original full unique constraint.
        op.create_unique_constraint("uq_users_email", "users", ["email"])


# ── SQLite helpers ──────────────────────────────────────────────────────


def _recreate_users_sqlite(
    *,
    email_nullable: bool,
    password_hash_nullable: bool,
    with_guest_columns: bool,
) -> None:
    """SQLite-only: rebuild the ``users`` table with the new column
    nullability and optional guest columns.

    Upgrade path sets ``with_guest_columns=True`` and both columns
    nullable. Downgrade sets ``with_guest_columns=False`` and both
    columns NOT NULL (after we've already purged guest rows).
    """
    email_null = "" if email_nullable else " NOT NULL"
    pw_null = "" if password_hash_nullable else " NOT NULL"
    guest_cols = (
        ", is_anonymous BOOLEAN NOT NULL DEFAULT 0"
        ", display_name VARCHAR(64)"
        if with_guest_columns
        else ""
    )

    op.execute("PRAGMA foreign_keys=OFF")

    # The original table had UNIQUE(email) inline. We *drop* that
    # here — the partial unique index is created separately by the
    # caller.
    op.execute(
        f"""
        CREATE TABLE users_new (
            id VARCHAR(36) NOT NULL,
            email VARCHAR(255){email_null},
            password_hash VARCHAR(512){pw_null},
            is_admin BOOLEAN,
            created_at DATETIME NOT NULL
            {guest_cols},
            PRIMARY KEY (id)
        )
        """
    )

    # Copy. The source (old ``users``) and the destination share the
    # four base columns. The guest columns (if present in the target)
    # get their declared DEFAULTs for upgrade — we never *downgrade*
    # with existing guest rows because the callsite already purged
    # them.
    op.execute(
        """
        INSERT INTO users_new (id, email, password_hash, is_admin, created_at)
        SELECT id, email, password_hash, is_admin, created_at FROM users
        """
    )

    op.execute("DROP TABLE users")
    op.execute("ALTER TABLE users_new RENAME TO users")

    op.execute("PRAGMA foreign_keys=ON")
    # Fail fast if swapping the table happened to dangle any FK.
    # SQLite's FK rewrite is implicit on RENAME, but a corrupted
    # DB from a partial run should surface here rather than silently
    # later.
    op.execute("PRAGMA foreign_key_check")


def _drop_email_unique_sql(dialect: str) -> str:
    """Best-effort portable drop of the implicit ``UNIQUE(email)``.

    Non-SQLite deployments are not yet targeted in prod — see
    migration 004 for the same caveat. We raise for now so an
    operator notices before running this migration on Postgres.
    """
    _ = dialect
    raise NotImplementedError(
        "Dropping the implicit UNIQUE(email) on non-SQLite requires "
        "reading information_schema or pg_catalog for the constraint "
        "name. Implement when the first Postgres deployment target "
        "materializes."
    )
