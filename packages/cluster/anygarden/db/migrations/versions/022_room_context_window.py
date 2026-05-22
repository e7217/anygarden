"""Add rooms.context_window_enabled column.

Revision ID: 022
Revises: 021
Create Date: 2026-04-19

Rationale
---------
Issue #148 — Stage B (env ``ANYGARDEN_CONTEXT_WINDOW_ENABLED``) only
supported machine-level on/off and required an agent restart to
flip. Operators asked for a per-room toggle so ``#general`` can
carry the ambient context window while a cost-sensitive DM stays
off.

A single nullable-false ``BOOLEAN`` column is the smallest shape
that expresses the "is this room broadcasting ambient context?"
question. Default ``FALSE`` preserves pre-#148 behaviour — rooms
created before the migration keep the server-side flag unset, and
the env-based Stage B path remains the only active ambient path
until Part 3 lands.

Note: the ``FALSE`` default was flipped to ``TRUE`` in migration
028 (#225, 2026-04-21) once Part 3 + per-agent opt-out (023) were
in place and ambient sharing became the default room experience.
Existing rows keep their stored value; only the DDL default
changed.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "022"
down_revision: str = "021"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ``batch_alter_table`` for SQLite compatibility. ``server_default``
    # is load-bearing: the batch helper refuses to add a NOT NULL
    # column to a non-empty table without one.
    with op.batch_alter_table("rooms") as batch:
        batch.add_column(
            sa.Column(
                "context_window_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.drop_column("context_window_enabled")
