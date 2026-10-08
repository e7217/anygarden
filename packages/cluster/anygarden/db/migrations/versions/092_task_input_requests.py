"""Add task_input_requests for general-room task questions (#806).

An agent that promoted the request it is answering into a task can ask the
user for missing facts. The question is posted in the task's source thread;
the first human reply answers it and resumes the same task.

Revision ID: 092_task_input_requests
Revises: 091_drop_room_speaker_strategy
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "092_task_input_requests"
down_revision = "091_drop_room_speaker_strategy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_input_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_key", sa.String(160), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requester_agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requester_participant_id", sa.String(36), sa.ForeignKey("participants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("turn_request_id", sa.String(36), sa.ForeignKey("agent_turns.request_id", ondelete="SET NULL"), nullable=True),
        sa.Column("question_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("answer_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resume_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("answered_at", UtcDateTime(), nullable=True),
        sa.UniqueConstraint("task_id", "question_key", name="uq_task_input_request_question"),
        sa.CheckConstraint("status IN ('pending', 'answered')", name="ck_task_input_request_status"),
    )
    op.create_index(
        "ix_task_input_requests_task_status", "task_input_requests", ["task_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_input_requests_task_status", table_name="task_input_requests")
    op.drop_table("task_input_requests")
