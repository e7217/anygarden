"""Keep a revision history of agent instructions (#908).

On machines that advertise ``agent_instructions_disk_v1`` the agent's
AGENTS.md and skills live on the machine's disk; ``agents.agents_md`` and
the ``skills/`` rows of ``agent_files`` become the last synced snapshot.
``instructions_revision`` advances on every accepted snapshot, and
``agent_instruction_revisions`` keeps each version with who made it so an
admin can restore one (agents may now edit their own instructions).

Revision ID: 095_agent_instruction_revisions
Revises: 094_sticky_agent_placement
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "095_agent_instruction_revisions"
down_revision = "094_sticky_agent_placement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column("instructions_revision", sa.Integer(), nullable=True, server_default="0")
        )
        batch.add_column(sa.Column("instructions_synced_at", UtcDateTime(), nullable=True))
    op.create_table(
        "agent_instruction_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("by_user_id", sa.String(36), nullable=True),
        sa.Column("restored_from", sa.Integer(), nullable=True),
        sa.Column("agents_md", sa.Text(), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.UniqueConstraint("agent_id", "revision", name="uq_agent_instruction_revision"),
    )
    op.create_index(
        "ix_agent_instruction_revisions_agent", "agent_instruction_revisions", ["agent_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_instruction_revisions_agent", table_name="agent_instruction_revisions")
    op.drop_table("agent_instruction_revisions")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("instructions_synced_at")
        batch.drop_column("instructions_revision")
