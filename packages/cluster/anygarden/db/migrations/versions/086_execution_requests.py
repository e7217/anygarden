"""Persist operating-room questions and same-task answer resumptions."""

import sqlalchemy as sa
from alembic import op
from anygarden.db.types import UtcDateTime

revision = "086_execution_requests"
down_revision = "085_project_executions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("project_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operating_room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_title", sa.String(500), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("question_key", sa.String(160), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requester_agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("requester_participant_id", sa.String(36), sa.ForeignKey("participants.id", ondelete="SET NULL")),
        sa.Column("turn_request_id", sa.String(36), sa.ForeignKey("agent_turns.request_id", ondelete="SET NULL")),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("agent_turn_attempts.id", ondelete="SET NULL")),
        sa.Column("question_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("answer_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("resume_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("answer", sa.Text()),
        sa.Column("answered_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("answered_at", UtcDateTime()),
        sa.UniqueConstraint("execution_id", "task_id", "input_revision", "question_key", name="uq_execution_request_question"),
        sa.CheckConstraint("status IN ('pending', 'answered')", name="ck_execution_request_status"),
    )
    op.create_index("ix_execution_requests_room_status", "execution_requests", ["operating_room_id", "status", "created_at"])
    op.create_index("ix_execution_requests_task_status", "execution_requests", ["task_id", "input_revision", "status"])


def downgrade() -> None:
    op.drop_table("execution_requests")
