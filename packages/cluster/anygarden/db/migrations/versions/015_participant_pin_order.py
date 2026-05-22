"""Add ``pinned`` and ``sort_order`` columns to ``participants`` (#47).

Revision ID: 015
Revises: 014
Create Date: 2026-04-15

Rationale
---------
Sidebar drag-and-drop reorder for pinned rooms. Each participant
row carries the pin state for its user in that room:

- ``pinned`` — whether the room shows in the sidebar's top pinned
  section (default ``FALSE``).
- ``sort_order`` — sparse integer position within the pinned
  section (NULL when not pinned). Spacing is 1024 so mid-list
  reorders don't need to renumber the whole list.

Composite index ``ix_participants_user_pinned_order`` makes the
"load my pinned rooms in order" query on each sidebar boot an
index-only scan.

SQLite-compatible: columns are added with ``server_default`` so
existing rows satisfy the NOT NULL constraint on ``pinned``. The
default is stripped on the ORM side so Python-level inserts keep
using the model default.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "015"
down_revision: str = "014"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("participants") as batch:
        batch.add_column(
            sa.Column(
                "pinned",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(
            sa.Column("sort_order", sa.Integer(), nullable=True)
        )
    op.create_index(
        "ix_participants_user_pinned_order",
        "participants",
        ["user_id", "pinned", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_participants_user_pinned_order", table_name="participants"
    )
    with op.batch_alter_table("participants") as batch:
        batch.drop_column("sort_order")
        batch.drop_column("pinned")
