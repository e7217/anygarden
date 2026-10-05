"""Version-bound human approval and its single managed action permit."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
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


class ExecutionApproval(Base):
    __tablename__ = "execution_approvals"
    __table_args__ = (
        UniqueConstraint("execution_id", "task_id", "input_revision", "action_key", name="uq_execution_approval_action"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'executing', 'succeeded', 'failed', 'unknown')", name="ck_execution_approval_status"),
        CheckConstraint("decision IS NULL OR decision IN ('approve', 'reject')", name="ck_execution_approval_decision"),
        Index("ix_execution_approvals_room_status", "operating_room_id", "status", "created_at"),
        Index("ix_execution_approvals_task_status", "task_id", "input_revision", "status"),
        Index("ix_execution_approvals_execution_deadline", "status", "execution_deadline_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(String(36), ForeignKey("project_executions.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    operating_room_id: Mapped[str] = mapped_column(String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    task_room_id: Mapped[str] = mapped_column(String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False)
    task_title: Mapped[str] = mapped_column(String(500), nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False)
    source_result_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_results.id", ondelete="RESTRICT"), nullable=False)
    source_result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(36), ForeignKey("room_artifacts.id", ondelete="RESTRICT"), nullable=False)
    artifact_room_id: Mapped[str] = mapped_column(String(36), nullable=False)
    artifact_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    action_key: Mapped[str] = mapped_column(String(160), nullable=False)
    action_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    target_alias: Mapped[str] = mapped_column(String(160), nullable=False)
    target_label: Mapped[str] = mapped_column(String(500), nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    action_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requester_agent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agents.id", ondelete="SET NULL"))
    requester_participant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("participants.id", ondelete="SET NULL"))
    turn_request_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agent_turns.request_id", ondelete="SET NULL"))
    decision: Mapped[str | None] = mapped_column(String(16))
    decided_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    request_message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("messages.id", ondelete="SET NULL"))
    decision_message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("messages.id", ondelete="SET NULL"))
    resume_message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("messages.id", ondelete="SET NULL"))
    result_message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("messages.id", ondelete="SET NULL"))
    executing_turn_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agent_turns.request_id", ondelete="SET NULL"))
    receipt: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    executed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    execution_deadline_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
