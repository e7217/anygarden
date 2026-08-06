"""Workspace attachment lease, epoch fencing, and chained audits.

Revision ID: 062
Revises: 061
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision: str = "062"
down_revision: str = "061"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("machines") as batch:
        batch.add_column(sa.Column("control_capabilities", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("workspace_catalog", sa.JSON(), nullable=True))

    op.create_table(
        "workspace_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("workspace_label", sa.String(80), nullable=False),
        sa.Column("machine_id", sa.String(36), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("room_id", sa.String(36), nullable=False),
        sa.Column("target_participant_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="requested"),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("allowlist_hash", sa.String(64), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("requested_by_user_id", sa.String(36), nullable=False),
        sa.Column("room_approved_by_user_id", sa.String(36), nullable=True),
        sa.Column("global_approved_by_user_id", sa.String(36), nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column(
            "resume_after_revoke",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column("activated_at", UtcDateTime(), nullable=True),
        sa.Column("revoked_at", UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["room_approved_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["global_approved_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_workspace_attachments_room_state",
        "workspace_attachments",
        ["room_id", "state"],
    )
    op.create_index(
        "ix_workspace_attachments_agent_state",
        "workspace_attachments",
        ["agent_id", "state"],
    )
    op.create_index(
        "ix_workspace_attachments_expiry",
        "workspace_attachments",
        ["state", "expires_at"],
    )
    op.create_index(
        "uq_workspace_attachment_agent_active",
        "workspace_attachments",
        ["agent_id"],
        unique=True,
        sqlite_where=sa.text(
            "state IN ('requested', 'machine_verified', 'active', 'revoking')"
        ),
        postgresql_where=sa.text(
            "state IN ('requested', 'machine_verified', 'active', 'revoking')"
        ),
    )

    op.create_table(
        "workspace_invocation_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("attachment_id", sa.String(36), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("actor_participant_id", sa.String(36), nullable=True),
        sa.Column("room_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("source_message_id", sa.String(36), nullable=True),
        sa.Column("source_thread_root_id", sa.String(36), nullable=True),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("machine_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("prompt_hmac", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("changed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["attachment_id"], ["workspace_attachments.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("row_hash", name="uq_workspace_audits_row_hash"),
    )
    op.create_index(
        "ix_workspace_audits_attachment_ts",
        "workspace_invocation_audits",
        ["attachment_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_audits_request",
        "workspace_invocation_audits",
        ["request_id"],
    )

    with op.batch_alter_table("agent_turns") as batch:
        batch.add_column(
            sa.Column("workspace_attachment_id", sa.String(36), nullable=True)
        )
        batch.add_column(
            sa.Column("workspace_attachment_epoch", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_agent_turns_workspace_attachment",
            "workspace_attachments",
            ["workspace_attachment_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_turns") as batch:
        batch.drop_constraint("fk_agent_turns_workspace_attachment", type_="foreignkey")
        batch.drop_column("workspace_attachment_epoch")
        batch.drop_column("workspace_attachment_id")
    op.drop_index(
        "ix_workspace_audits_request", table_name="workspace_invocation_audits"
    )
    op.drop_index(
        "ix_workspace_audits_attachment_ts",
        table_name="workspace_invocation_audits",
    )
    op.drop_table("workspace_invocation_audits")
    op.drop_index(
        "uq_workspace_attachment_agent_active", table_name="workspace_attachments"
    )
    op.drop_index("ix_workspace_attachments_expiry", table_name="workspace_attachments")
    op.drop_index(
        "ix_workspace_attachments_agent_state", table_name="workspace_attachments"
    )
    op.drop_index(
        "ix_workspace_attachments_room_state", table_name="workspace_attachments"
    )
    op.drop_table("workspace_attachments")
    with op.batch_alter_table("machines") as batch:
        batch.drop_column("workspace_catalog")
        batch.drop_column("control_capabilities")
