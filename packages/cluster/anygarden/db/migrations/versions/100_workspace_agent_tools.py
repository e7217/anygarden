"""Workspace tools for agents and the approval queue (#938).

``agents.workspace_tools`` exposes ``workspace_query`` / ``workspace_action``
to that agent; ``agents.can_create_agents`` lets it propose hiring.
``workspace_action_requests`` holds the actions waiting for a person.

Revision ID: 100_workspace_agent_tools
Revises: 099_campaign_eval_evaluator_digest
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "100_workspace_agent_tools"
down_revision = "099_campaign_eval_evaluator_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("workspace_tools", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch.add_column(sa.Column("can_create_agents", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.create_table(
        "workspace_action_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_participant_id", sa.String(36), sa.ForeignKey("participants.id", ondelete="SET NULL")),
        sa.Column("room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_root_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("requested_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("turn_request_id", sa.String(36)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("request_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("decided_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", UtcDateTime()),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("decided_at", UtcDateTime()),
        sa.CheckConstraint(
            "status IN ('pending', 'executing', 'executed', 'rejected', 'expired', 'failed')",
            name="ck_workspace_action_status",
        ),
    )
    op.create_index(
        "ix_workspace_action_room_status", "workspace_action_requests", ["room_id", "status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_action_room_status", table_name="workspace_action_requests")
    op.drop_table("workspace_action_requests")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("can_create_agents")
        batch.drop_column("workspace_tools")
