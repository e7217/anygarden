"""Typed recovery actions for delegations (D-5, #628)."""

import sqlalchemy as sa
from alembic import op

revision = "070_recovery_actions"
down_revision = "069_reactions_wake_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "federation_recovery_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("authority_node_id", sa.String(36), nullable=False),
        sa.Column("channel_id", sa.String(36), nullable=False),
        sa.Column(
            "delegation_id",
            sa.String(36),
            sa.ForeignKey("federation_delegations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_recovery_actions_pending",
        "federation_recovery_actions",
        ["authority_node_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recovery_actions_pending", table_name="federation_recovery_actions"
    )
    op.drop_table("federation_recovery_actions")
