"""Workspace actions an agent asked a person to approve (#938)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from anygarden.db.models import Base, _utcnow, _uuid
from anygarden.db.types import UtcDateTime

WORKSPACE_ACTION_STATUSES = ("pending", "executing", "executed", "rejected", "expired", "failed")


class WorkspaceActionRequest(Base):
    """One ``workspace_action`` call held for a person's decision.

    The stored ``params`` run once, with the approver's permissions, when
    the request is approved; ``executing`` is the claim that keeps a second
    approval from running it again.
    """

    __tablename__ = "workspace_action_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'executing', 'executed', 'rejected', 'expired', 'failed')",
            name="ck_workspace_action_status",
        ),
        Index("ix_workspace_action_room_status", "room_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    agent_participant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("participants.id", ondelete="SET NULL")
    )
    room_id: Mapped[str] = mapped_column(String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    thread_root_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("messages.id", ondelete="SET NULL"))
    requested_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    turn_request_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    request_message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("messages.id", ondelete="SET NULL"))
    decided_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
