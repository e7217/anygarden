"""Message emoji reactions and per-room wake trigger policy (D-1, issue #624).

Adds ``message_reactions`` — the low-cost emoji receipt surface. Reactions are
receipts only: the WS layer broadcasts them as reaction events, and reaction
events are structurally prevented from waking channel agents (the attention
norm is enforced server-side, not left to clients).

Adds ``rooms.wake_triggers`` — the per-room policy listing which wake
triggers (``message`` / ``mention`` / ``reminder``) reach channel agents.
Default ``["mention", "reminder"]``; ``message`` is opt-in. Admin-gated via
the normal room settings path.
"""

import sqlalchemy as sa
from alembic import op

revision = "069_reactions_wake_triggers"
down_revision = "068_agent_quota_availability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_reactions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(length=36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "participant_id",
            sa.String(length=36),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("emoji", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "message_id",
            "participant_id",
            "emoji",
            name="uq_message_participant_emoji",
        ),
    )
    op.create_index(
        "ix_message_reactions_message",
        "message_reactions",
        ["message_id"],
    )
    with op.batch_alter_table("rooms") as batch:
        batch.add_column(
            sa.Column(
                "wake_triggers",
                sa.JSON(),
                nullable=False,
                server_default='["mention", "reminder"]',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.drop_column("wake_triggers")
    op.drop_index("ix_message_reactions_message", table_name="message_reactions")
    op.drop_table("message_reactions")
