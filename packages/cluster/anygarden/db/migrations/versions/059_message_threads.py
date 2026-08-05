"""Add top-level message threads and participant thread state.

Revision ID: 059
Revises: 058
Create Date: 2026-08-05

Existing rows already have the root representation (NULL/NULL), so the
migration is additive and requires no data or FTS backfill.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "059"
down_revision: str = "058"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # SQLite needs batch mode to add named self-referential foreign keys and
    # the shape CHECK while retaining the existing room/participant FKs.
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("parent_message_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("root_message_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_messages_parent_message_id",
            "messages",
            ["parent_message_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_messages_root_message_id",
            "messages",
            ["root_message_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_messages_thread_shape",
            "((parent_message_id IS NULL AND root_message_id IS NULL) OR "
            "(parent_message_id IS NOT NULL AND "
            "parent_message_id = root_message_id))",
        )

    op.create_index(
        "ix_messages_room_root_seq",
        "messages",
        ["room_id", "root_message_id", "seq"],
    )

    op.create_table(
        "thread_participant_states",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("participant_id", sa.String(36), nullable=False),
        sa.Column("root_message_id", sa.String(36), nullable=False),
        sa.Column("last_read_seq", sa.BigInteger(), nullable=True),
        sa.Column("followed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name="fk_thread_participant_states_participant_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["root_message_id"],
            ["messages.id"],
            name="fk_thread_participant_states_root_message_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "participant_id",
            "root_message_id",
            name="uq_thread_participant_state",
        ),
    )
    op.create_index(
        "ix_thread_participant_states_root_read",
        "thread_participant_states",
        ["root_message_id", "last_read_seq"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_thread_participant_states_root_read",
        table_name="thread_participant_states",
    )
    op.drop_table("thread_participant_states")
    op.drop_index("ix_messages_room_root_seq", table_name="messages")
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint(
            "ck_messages_thread_shape",
            type_="check",
        )
        batch.drop_constraint(
            "fk_messages_root_message_id",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_messages_parent_message_id",
            type_="foreignkey",
        )
        batch.drop_column("root_message_id")
        batch.drop_column("parent_message_id")
