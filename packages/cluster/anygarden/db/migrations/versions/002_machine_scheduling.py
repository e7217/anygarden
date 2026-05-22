"""Machine scheduling — engines, tokens, agent lifecycle.

Revision ID: 002
Revises: 001
Create Date: 2024-01-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: str = "001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- machine_engines ---
    op.create_table(
        "machine_engines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "machine_id",
            sa.String(36),
            sa.ForeignKey("machines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_machine_engines_engine", "machine_engines", ["engine"])

    # --- machine_tokens ---
    op.create_table(
        "machine_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "machine_id",
            sa.String(36),
            sa.ForeignKey("machines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(512), nullable=False),
        sa.Column("lookup_hint", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_machine_tokens_hint", "machine_tokens", ["lookup_hint"])

    # --- Add columns to agents ---
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(sa.Column("pid", sa.Integer, nullable=True))
        batch_op.add_column(
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_crash_reason", sa.Text, nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "restart_policy",
                sa.String(64),
                nullable=False,
                server_default="restart_anywhere",
            )
        )
        batch_op.create_index(
            "ix_agents_placed_state",
            ["placed_on_machine_id", "actual_state"],
        )

    # --- Add columns to machines ---
    with op.batch_alter_table("machines") as batch_op:
        batch_op.add_column(sa.Column("daemon_version", sa.String(64), nullable=True))
        batch_op.create_index(
            "ix_machines_status_owner",
            ["status", "owner_user_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("machines") as batch_op:
        batch_op.drop_index("ix_machines_status_owner")
        batch_op.drop_column("daemon_version")

    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_index("ix_agents_placed_state")
        batch_op.drop_column("restart_policy")
        batch_op.drop_column("last_crash_reason")
        batch_op.drop_column("last_heartbeat_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("pid")

    op.drop_table("machine_tokens")
    op.drop_table("machine_engines")
