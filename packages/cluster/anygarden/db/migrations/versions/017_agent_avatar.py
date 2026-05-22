"""Add agents.avatar_kind and agents.avatar_value columns.

Revision ID: 017
Revises: 016
Create Date: 2026-04-18

Rationale
---------
Issue #101 — the admin UI needs per-agent avatars beyond the seed-
driven initials that PR #99 introduced. Rather than bake a kind
discriminator into a single string via prefixes ("emoji:…",
"lucide:…"), the row stores the discriminator and the value
separately so every reader can branch on ``avatar_kind`` without
parsing. Future avatar sources (image upload, @lobehub brand
marks) will add new ``avatar_kind`` values rather than a new
column.

Both columns are nullable. ``NULL / NULL`` means "no custom
avatar" and the UI falls back to the seed-driven initial. An
explicit enum/CHECK is avoided here for the same reason 016 cited:
alembic-on-sqlite's enum migration path is awkward, and the set
of allowed values is enforced at the Pydantic / UI layer.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: str = "016"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ``batch_alter_table`` for SQLite compatibility — the table
    # recreation dance the batch helper performs is a no-op on
    # Postgres but essential on SQLite.
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column("avatar_kind", sa.String(16), nullable=True, default=None)
        )
        batch.add_column(
            sa.Column("avatar_value", sa.String(64), nullable=True, default=None)
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("avatar_value")
        batch.drop_column("avatar_kind")
