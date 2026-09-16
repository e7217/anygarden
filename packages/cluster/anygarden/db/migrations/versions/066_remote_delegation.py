"""Durable remote task decisions and executor outbox (#592)."""

import sqlalchemy as sa
from alembic import op

revision = "066_remote_delegation"
down_revision = "065_shared_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "federation_delegations",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("authority_node_id", sa.String(length=36), nullable=False),
        sa.Column(
            "channel_id",
            sa.String(length=36),
            sa.ForeignKey("rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_message_id", sa.String(length=36), nullable=False),
        sa.Column("requester", sa.JSON(), nullable=False),
        sa.Column("executor_node_id", sa.String(length=36), nullable=False),
        sa.Column("executor_agent_id", sa.String(length=36), nullable=False),
        sa.Column("executor_participant_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("process_state", sa.String(length=32), nullable=False),
        sa.UniqueConstraint(
            "executor_node_id", "execution_id", name="uq_delegation_execution"
        ),
    )
    op.create_table(
        "federation_delegation_reservations",
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "delegation_id",
            sa.String(length=36),
            sa.ForeignKey("federation_delegations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("delegation_id"),
    )
    op.create_table(
        "federation_delegation_observations",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "delegation_id",
            sa.String(length=36),
            sa.ForeignKey("federation_delegations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.UniqueConstraint(
            "delegation_id", "request_id", name="uq_delegation_observation_request"
        ),
    )
    op.create_table(
        "federation_executor_bindings",
        sa.Column(
            "delegation_id", sa.String(length=36), nullable=False, primary_key=True
        ),
        sa.Column("authority_node_id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("invocation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("grant_epoch", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("authority_state", sa.String(length=32), nullable=False),
        sa.Column("local_state", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("execution_id"),
    )
    op.create_table(
        "federation_delegation_outbox",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "delegation_id",
            sa.String(length=36),
            sa.ForeignKey(
                "federation_executor_bindings.delegation_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("canonical_body", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_federation_delegation_outbox_delegation_id",
        "federation_delegation_outbox",
        ["delegation_id"],
        unique=False,
    )

    op.create_table(
        "federation_delegation_mirrors",
        sa.Column("authority_node_id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("channel_id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("delegation_id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("source_message_id", sa.String(36), nullable=False),
        sa.Column("requester", sa.JSON(), nullable=False),
        sa.Column("executor", sa.JSON(), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("process_state", sa.String(32), nullable=False),
        sa.Column("task_status", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("federation_delegation_mirrors")
    op.drop_table("federation_delegation_outbox")
    op.drop_table("federation_executor_bindings")
    op.drop_table("federation_delegation_observations")
    op.drop_table("federation_delegation_reservations")
    op.drop_table("federation_delegations")
