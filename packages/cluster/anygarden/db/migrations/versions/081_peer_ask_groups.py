"""Persist ask_peer question groups for the async fan-in (issue #762).

``ask_peer`` used to keep scheduled questions in memory and post them after
the caller's final reply, so the caller never saw the answers. The fan-in
posts questions at once, ends the caller turn, and wakes the caller again
when every peer turn is terminal. Peer work can take minutes, so the group
state has to survive a restart.
"""

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "081_peer_ask_groups"
down_revision = "080_drop_agent_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "peer_ask_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "room_id",
            sa.String(36),
            sa.ForeignKey("rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "caller_participant_id",
            sa.String(36),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "caller_agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("caller_request_id", sa.String(36), nullable=False),
        sa.Column(
            "trigger_message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "scope_thread_root_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "state", sa.String(16), nullable=False, server_default="collecting"
        ),
        sa.Column("caller_closed_at", UtcDateTime(), nullable=True),
        sa.Column("caller_draft", sa.Text(), nullable=True),
        sa.Column("deadline_at", UtcDateTime(), nullable=False),
        sa.Column("wake_request_id", sa.String(36), nullable=True),
        sa.Column("woken_at", UtcDateTime(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=True),
        sa.UniqueConstraint(
            "caller_request_id", name="uq_peer_ask_groups_caller_request"
        ),
    )
    op.create_index(
        "ix_peer_ask_groups_state_deadline",
        "peer_ask_groups",
        ["state", "deadline_at"],
    )
    op.create_table(
        "peer_ask_targets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "group_id",
            sa.String(36),
            sa.ForeignKey("peer_ask_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_participant_id",
            sa.String(36),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "question_message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.String(128), nullable=True),
        sa.Column(
            "reply_message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("forwarded_requests", sa.JSON(), nullable=False),
        sa.Column("finished_at", UtcDateTime(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=True),
        sa.UniqueConstraint("request_id", name="uq_peer_ask_targets_request"),
    )
    op.create_index("ix_peer_ask_targets_group", "peer_ask_targets", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_peer_ask_targets_group", table_name="peer_ask_targets")
    op.drop_table("peer_ask_targets")
    op.drop_index("ix_peer_ask_groups_state_deadline", table_name="peer_ask_groups")
    op.drop_table("peer_ask_groups")
