"""Authority decisions and executor delivery intent for shared tasks (#592)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from anygarden.db.models import Base


class Delegation(Base):
    __tablename__ = "federation_delegations"
    __table_args__ = (
        UniqueConstraint(
            "executor_node_id", "execution_id", name="uq_delegation_execution"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    authority_node_id: Mapped[str] = mapped_column(String(36))
    channel_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    source_message_id: Mapped[str] = mapped_column(String(36))
    requester: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    executor_node_id: Mapped[str] = mapped_column(String(36))
    executor_agent_id: Mapped[str] = mapped_column(String(36))
    executor_participant_id: Mapped[str] = mapped_column(String(36))
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    process_state: Mapped[str] = mapped_column(String(32))


class DelegationReservation(Base):
    __tablename__ = "federation_delegation_reservations"
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    delegation_id: Mapped[str] = mapped_column(
        ForeignKey("federation_delegations.id", ondelete="CASCADE"), unique=True
    )


class DelegationObservation(Base):
    __tablename__ = "federation_delegation_observations"
    __table_args__ = (
        UniqueConstraint(
            "delegation_id", "request_id", name="uq_delegation_observation_request"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    delegation_id: Mapped[str] = mapped_column(
        ForeignKey("federation_delegations.id", ondelete="CASCADE")
    )
    request_id: Mapped[str] = mapped_column(String(36))
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reason: Mapped[str] = mapped_column(String(32))


class ExecutorBinding(Base):
    __tablename__ = "federation_executor_bindings"
    delegation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    authority_node_id: Mapped[str] = mapped_column(String(36))
    channel_id: Mapped[str] = mapped_column(String(36))
    agent_id: Mapped[str] = mapped_column(String(36))
    generation: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[str] = mapped_column(String(64))
    execution_id: Mapped[str] = mapped_column(String(36), unique=True)
    invocation_fingerprint: Mapped[str] = mapped_column(String(64))
    scope: Mapped[dict] = mapped_column(JSON)
    grant_epoch: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    authority_state: Mapped[str] = mapped_column(String(32))
    local_state: Mapped[str] = mapped_column(String(32))


class DelegationOutbox(Base):
    __tablename__ = "federation_delegation_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    delegation_id: Mapped[str] = mapped_column(
        ForeignKey("federation_executor_bindings.delegation_id", ondelete="CASCADE"),
        index=True,
    )
    command: Mapped[dict] = mapped_column(JSON)
    canonical_body: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), default="pending")
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DelegationMirror(Base):
    """Authority-confirmed follower view; never the local Task authority."""

    __tablename__ = "federation_delegation_mirrors"
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    delegation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36))
    source_message_id: Mapped[str] = mapped_column(String(36))
    requester: Mapped[dict] = mapped_column(JSON)
    executor: Mapped[dict] = mapped_column(JSON)
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32))
    process_state: Mapped[str] = mapped_column(String(32))
    task_status: Mapped[str] = mapped_column(String(32))
