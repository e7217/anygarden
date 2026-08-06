"""Durable agent turns, attempts, outbox, and generation drain state.

Revision ID: 061
Revises: 060
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from anygarden.db.types import UtcDateTime

revision: str = "061"
down_revision: str = "060"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("pending_generation", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("restart_requested_at", UtcDateTime(), nullable=True)
        )
        batch.add_column(sa.Column("restart_deadline_at", UtcDateTime(), nullable=True))
        batch.add_column(sa.Column("manifest_hash", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("pending_manifest_hash", sa.String(64), nullable=True)
        )

    op.create_table(
        "agent_turns",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("room_id", sa.String(36), nullable=False),
        sa.Column("target_participant_id", sa.String(36), nullable=True),
        sa.Column("agent_id", sa.String(36), nullable=True),
        sa.Column("trigger_message_id", sa.String(36), nullable=True),
        sa.Column("thread_root_id", sa.String(36), nullable=True),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active_attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("accepted_message_id", sa.String(36), nullable=True),
        sa.Column("terminal_reason", sa.String(128), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column("completed_at", UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["target_participant_id"], ["participants.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["trigger_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["thread_root_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["accepted_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_turns_idempotency_key"),
    )
    op.create_index("ix_agent_turns_agent_state", "agent_turns", ["agent_id", "state"])
    op.create_index("ix_agent_turns_room_state", "agent_turns", ["room_id", "state"])

    op.create_table(
        "agent_turn_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("leased_at", UtcDateTime(), nullable=True),
        sa.Column("started_at", UtcDateTime(), nullable=True),
        sa.Column("ended_at", UtcDateTime(), nullable=True),
        sa.Column("lease_expires_at", UtcDateTime(), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("reason", sa.String(128), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["agent_turns.request_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "turn_id", "attempt_number", name="uq_agent_turn_attempt_number"
        ),
        sa.UniqueConstraint("lease_token", name="uq_agent_turn_attempt_lease"),
    )
    op.create_index(
        "ix_agent_turn_attempts_agent_state",
        "agent_turn_attempts",
        ["agent_id", "state"],
    )
    op.create_index(
        "ix_agent_turn_attempts_lease_expiry",
        "agent_turn_attempts",
        ["state", "lease_expires_at"],
    )
    op.create_index(
        "uq_agent_turn_attempt_active",
        "agent_turn_attempts",
        ["turn_id"],
        unique=True,
        sqlite_where=sa.text("state IN ('leased', 'started')"),
        postgresql_where=sa.text("state IN ('leased', 'started')"),
    )

    op.create_table(
        "agent_turn_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("room_id", sa.String(36), nullable=False),
        sa.Column("participant_id", sa.String(36), nullable=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("delivery_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", UtcDateTime(), nullable=False),
        sa.Column("delivered_at", UtcDateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["agent_turns.request_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["agent_turn_attempts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["participant_id"], ["participants.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("attempt_id", name="uq_agent_turn_outbox_attempt"),
    )
    op.create_index(
        "ix_agent_turn_outbox_state_available",
        "agent_turn_outbox",
        ["state", "available_at"],
    )
    op.create_index(
        "ix_agent_turn_outbox_participant_state",
        "agent_turn_outbox",
        ["participant_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_turn_outbox_participant_state", table_name="agent_turn_outbox"
    )
    op.drop_index(
        "ix_agent_turn_outbox_state_available", table_name="agent_turn_outbox"
    )
    op.drop_table("agent_turn_outbox")
    op.drop_index("uq_agent_turn_attempt_active", table_name="agent_turn_attempts")
    op.drop_index(
        "ix_agent_turn_attempts_lease_expiry", table_name="agent_turn_attempts"
    )
    op.drop_index(
        "ix_agent_turn_attempts_agent_state", table_name="agent_turn_attempts"
    )
    op.drop_table("agent_turn_attempts")
    op.drop_index("ix_agent_turns_room_state", table_name="agent_turns")
    op.drop_index("ix_agent_turns_agent_state", table_name="agent_turns")
    op.drop_table("agent_turns")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("pending_manifest_hash")
        batch.drop_column("manifest_hash")
        batch.drop_column("restart_deadline_at")
        batch.drop_column("restart_requested_at")
        batch.drop_column("pending_generation")
