"""Once-only interaction resolution registry (D-6, #629)."""

import sqlalchemy as sa
from alembic import op

revision = "071_interaction_resolutions"
down_revision = "070_recovery_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interaction_resolutions",
        sa.Column("interaction_id", sa.String(36), primary_key=True),
        sa.Column(
            "room_id",
            sa.String(36),
            sa.ForeignKey("rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_message_id", sa.String(36), nullable=False),
        sa.Column("resolution_message_id", sa.String(36), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("interaction_resolutions")
