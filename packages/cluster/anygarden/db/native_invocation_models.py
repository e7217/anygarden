"""One durable accounting row per permitted native invocation.

This ledger counts native start permissions, not provider-internal calls.
Exact reservation/adoption and immutable settlement are enforced by the
transactional accounting service; missing usage never defaults to zero.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from anygarden.db.models import Base, _utcnow, _uuid
from anygarden.db.types import UtcDateTime


class NativeInvocationAccounting(Base):
    __tablename__ = "native_invocation_accounting"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_native_accounting_attempt"),
        UniqueConstraint(
            "local_execution_id", name="uq_native_accounting_local_execution"
        ),
        CheckConstraint(
            "usage_status IN ('pending', 'measured', 'unknown', 'not_started')",
            name="ck_native_accounting_usage_status",
        ),
        CheckConstraint(
            "(execution_id IS NULL AND input_revision IS NULL) OR "
            "(execution_id IS NOT NULL AND input_revision IS NOT NULL AND input_revision >= 1)",
            name="ck_native_accounting_execution_binding",
        ),
        CheckConstraint(
            "attempt_number >= 1", name="ck_native_accounting_attempt_number"
        ),
        CheckConstraint("generation >= 0", name="ck_native_accounting_generation"),
        CheckConstraint(
            "(usage_status = 'pending' AND settled_at IS NULL AND settlement_sha256 IS NULL) OR "
            "(usage_status <> 'pending' AND settled_at IS NOT NULL AND settlement_sha256 IS NOT NULL)",
            name="ck_native_accounting_settlement",
        ),
        CheckConstraint(
            "settlement_sha256 IS NULL OR length(settlement_sha256) = 64",
            name="ck_native_accounting_settlement_sha256",
        ),
        *(
            CheckConstraint(
                f"{field} IS NULL OR ({field} >= 0 AND {field} <= 9223372036854775807)",
                name=f"ck_native_accounting_{field}",
            )
            for field in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "duration_ms",
            )
        ),
        CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0", name="ck_native_accounting_cost_usd"
        ),
        Index(
            "ix_native_accounting_execution_revision", "execution_id", "input_revision"
        ),
        Index("ix_native_accounting_request", "request_id", "attempt_number"),
        Index("ix_native_accounting_usage_status", "usage_status", "reserved_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    attempt_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_turn_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    local_execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_turns.request_id", ondelete="RESTRICT"),
        nullable=False,
    )
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True
    )
    execution_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("project_executions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    input_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow
    )
    settled_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    as_of: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=_utcnow
    )
    usage_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    closure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closure_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usage_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    native_terminal: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    settlement_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
