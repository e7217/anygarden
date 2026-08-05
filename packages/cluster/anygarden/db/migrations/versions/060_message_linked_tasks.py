"""Link tasks to source messages.

Revision ID: 060
Revises: 059
Create Date: 2026-08-05

Legacy and Goal-derived tasks intentionally retain a NULL source.  The
foreign key uses SET NULL so deleting an otherwise deletable legacy message
does not delete task history.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "060"
down_revision: str = "059"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("source_message_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_source_message_id",
            "messages",
            ["source_message_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_tasks_source_message_id",
            ["source_message_id"],
        )

    op.create_index(
        "ix_tasks_source_message_id",
        "tasks",
        ["source_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_source_message_id", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("uq_tasks_source_message_id", type_="unique")
        batch.drop_constraint("fk_tasks_source_message_id", type_="foreignkey")
        batch.drop_column("source_message_id")
