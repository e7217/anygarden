"""Separate room memory while retaining the unattributed global archive."""

import sqlalchemy as sa
from alembic import op
from anygarden.db.types import UtcDateTime

revision = "088_agent_room_memory"
down_revision = "087_execution_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_room_memories",
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("memory_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_agent_room_memory_revision"),
        sa.CheckConstraint("session_epoch >= 0", name="ck_agent_room_memory_epoch"),
    )
    op.create_index("ix_agent_room_memories_room", "agent_room_memories", ["room_id"])
    # Deliberately no backfill: Agent.memory_md has no trustworthy room source.


def downgrade() -> None:
    op.drop_table("agent_room_memories")
