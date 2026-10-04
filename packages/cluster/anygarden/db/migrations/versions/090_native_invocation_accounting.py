"""Exact native permit reservations and nullable immutable usage settlement."""

import sqlalchemy as sa
from alembic import op
from anygarden.db.types import UtcDateTime

revision = "090_native_invocation_accounting"
down_revision = "089_execution_mutations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_executions",
        sa.Column(
            "native_invocations_reserved",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_table(
        "native_invocation_accounting",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("local_execution_id", sa.String(36), nullable=False),
        sa.Column(
            "request_id",
            sa.String(36),
            sa.ForeignKey("agent_turns.request_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "room_id",
            sa.String(36),
            sa.ForeignKey("rooms.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("project_executions.id", ondelete="RESTRICT"),
        ),
        sa.Column("input_revision", sa.Integer()),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("reserved_at", UtcDateTime(), nullable=False),
        sa.Column("settled_at", UtcDateTime()),
        sa.Column("as_of", UtcDateTime(), nullable=False),
        sa.Column(
            "usage_status", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column("closure_reason", sa.String(64)),
        sa.Column("closure_source", sa.String(64)),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("cached_input_tokens", sa.BigInteger()),
        sa.Column("cost_usd", sa.Numeric(20, 9)),
        sa.Column("model", sa.String(160)),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("usage_metadata", sa.JSON()),
        sa.Column("native_terminal", sa.JSON()),
        sa.Column("settlement_sha256", sa.String(64)),
        sa.UniqueConstraint("attempt_id", name="uq_native_accounting_attempt"),
        sa.UniqueConstraint(
            "local_execution_id", name="uq_native_accounting_local_execution"
        ),
        sa.CheckConstraint(
            "usage_status IN ('pending', 'measured', 'unknown', 'not_started')",
            name="ck_native_accounting_usage_status",
        ),
        sa.CheckConstraint(
            "(execution_id IS NULL AND input_revision IS NULL) OR "
            "(execution_id IS NOT NULL AND input_revision IS NOT NULL AND input_revision >= 1)",
            name="ck_native_accounting_execution_binding",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name="ck_native_accounting_attempt_number"
        ),
        sa.CheckConstraint("generation >= 0", name="ck_native_accounting_generation"),
        sa.CheckConstraint(
            "(usage_status = 'pending' AND settled_at IS NULL AND settlement_sha256 IS NULL) OR "
            "(usage_status <> 'pending' AND settled_at IS NOT NULL AND settlement_sha256 IS NOT NULL)",
            name="ck_native_accounting_settlement",
        ),
        sa.CheckConstraint(
            "settlement_sha256 IS NULL OR length(settlement_sha256) = 64",
            name="ck_native_accounting_settlement_sha256",
        ),
        *(
            sa.CheckConstraint(
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
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0", name="ck_native_accounting_cost_usd"
        ),
    )
    op.create_index(
        "ix_native_accounting_execution_revision",
        "native_invocation_accounting",
        ["execution_id", "input_revision"],
    )
    op.create_index(
        "ix_native_accounting_request",
        "native_invocation_accounting",
        ["request_id", "attempt_number"],
    )
    op.create_index(
        "ix_native_accounting_usage_status",
        "native_invocation_accounting",
        ["usage_status", "reserved_at"],
    )


def downgrade() -> None:
    op.drop_table("native_invocation_accounting")
    op.drop_column("project_executions", "native_invocations_reserved")
