"""Add agent_tokens table for O(1) agent authentication.

Revision ID: 003
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(512), nullable=False),
        sa.Column("lookup_hint", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_tokens_hint", "agent_tokens", ["lookup_hint"])


def downgrade() -> None:
    op.drop_index("ix_agent_tokens_hint", table_name="agent_tokens")
    op.drop_table("agent_tokens")
