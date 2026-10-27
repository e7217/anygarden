"""Goal and routine drafts an agent writes for a person (#947, #948).

``workspace_draft_requests`` records a ``/goal`` or ``/routine`` request:
which agent drafts it, the turn in which it may submit the draft, and how
it ended.

Revision ID: 101_workspace_draft_requests
Revises: 100_workspace_agent_tools
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "101_workspace_draft_requests"
down_revision = "100_workspace_agent_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_draft_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("drafter_agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("drafter_participant_id", sa.String(36), sa.ForeignKey("participants.id", ondelete="SET NULL")),
        sa.Column("assignee_agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(64)),
        sa.Column("request_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("turn_request_id", sa.String(36)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "action_request_id", sa.String(36),
            sa.ForeignKey("workspace_action_requests.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", UtcDateTime()),
        sa.Column("closed_at", UtcDateTime()),
        sa.CheckConstraint("kind IN ('goal', 'routine')", name="ck_workspace_draft_kind"),
        sa.CheckConstraint("status IN ('pending', 'proposed', 'failed')", name="ck_workspace_draft_status"),
    )
    op.create_index("ix_workspace_draft_status", "workspace_draft_requests", ["status", "created_at"])
    op.create_index("ix_workspace_draft_turn", "workspace_draft_requests", ["turn_request_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_draft_turn", table_name="workspace_draft_requests")
    op.drop_index("ix_workspace_draft_status", table_name="workspace_draft_requests")
    op.drop_table("workspace_draft_requests")
