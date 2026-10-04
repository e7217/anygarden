"""Task- and input-version-bound questions answered in the operating room."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from anygarden.db.models import Base, _utcnow, _uuid
from anygarden.db.types import UtcDateTime


class ExecutionRequest(Base):
    __tablename__ = "execution_requests"
    __table_args__ = (
        UniqueConstraint(
            "execution_id", "task_id", "input_revision", "question_key",
            name="uq_execution_request_question",
        ),
        CheckConstraint("status IN ('pending', 'answered')", name="ck_execution_request_status"),
        Index("ix_execution_requests_room_status", "operating_room_id", "status", "created_at"),
        Index("ix_execution_requests_task_status", "task_id", "input_revision", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("project_executions.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    operating_room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    task_room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False
    )
    task_title: Mapped[str] = mapped_column(String(500), nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    question_key: Mapped[str] = mapped_column(String(160), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requester_agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    requester_participant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("participants.id", ondelete="SET NULL"), nullable=True
    )
    turn_request_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_turns.request_id", ondelete="SET NULL"), nullable=True
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_turn_attempts.id", ondelete="SET NULL"), nullable=True
    )
    question_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    answer_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    resume_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=_utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
