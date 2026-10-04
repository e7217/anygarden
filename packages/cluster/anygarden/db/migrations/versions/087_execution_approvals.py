"""Persist artifact-version approvals and single-use managed action permits."""

import sqlalchemy as sa
from alembic import op
from anygarden.db.types import UtcDateTime

revision = "087_execution_approvals"
down_revision = "086_execution_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("project_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operating_room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_title", sa.String(500), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("source_task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_result_id", sa.String(36), sa.ForeignKey("task_results.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_result_version", sa.Integer(), nullable=False),
        sa.Column("source_result_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("room_artifacts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("artifact_room_id", sa.String(36), nullable=False),
        sa.Column("artifact_filename", sa.String(255), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("action_key", sa.String(160), nullable=False),
        sa.Column("action_kind", sa.String(48), nullable=False),
        sa.Column("target_alias", sa.String(160), nullable=False),
        sa.Column("target_label", sa.String(500), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("action_digest", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requester_agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("requester_participant_id", sa.String(36), sa.ForeignKey("participants.id", ondelete="SET NULL")),
        sa.Column("turn_request_id", sa.String(36), sa.ForeignKey("agent_turns.request_id", ondelete="SET NULL")),
        sa.Column("decision", sa.String(16)),
        sa.Column("decided_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("request_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("decision_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("resume_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("result_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("executing_turn_id", sa.String(36), sa.ForeignKey("agent_turns.request_id", ondelete="SET NULL")),
        sa.Column("receipt", sa.JSON()),
        sa.Column("error", sa.String(160)),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("decided_at", UtcDateTime()),
        sa.Column("executed_at", UtcDateTime()),
        sa.Column("execution_deadline_at", UtcDateTime()),
        sa.Column("finished_at", UtcDateTime()),
        sa.UniqueConstraint("execution_id", "task_id", "input_revision", "action_key", name="uq_execution_approval_action"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'executing', 'succeeded', 'failed', 'unknown')", name="ck_execution_approval_status"),
        sa.CheckConstraint("decision IS NULL OR decision IN ('approve', 'reject')", name="ck_execution_approval_decision"),
    )
    op.create_index("ix_execution_approvals_room_status", "execution_approvals", ["operating_room_id", "status", "created_at"])
    op.create_index("ix_execution_approvals_task_status", "execution_approvals", ["task_id", "input_revision", "status"])
    op.create_index("ix_execution_approvals_execution_deadline", "execution_approvals", ["status", "execution_deadline_at"])


def downgrade() -> None:
    op.drop_table("execution_approvals")
