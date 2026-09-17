"""Delegation pickup-timeout sweep support (#594 follow-up, task #58).

Adds the delegation creation timestamp used as the pickup-timeout basis and
makes the audit observation's execution nullable: a delegation that never
reached acceptance has no execution to reference.
"""

import sqlalchemy as sa
from alembic import op

revision = "067_delegation_pickup_timeout"
down_revision = "066_remote_delegation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy rows are stamped with the migration time; their pickup window
    # starts at upgrade. New rows default to insertion time.
    with op.batch_alter_table("federation_delegations") as batch:
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
    with op.batch_alter_table("federation_delegation_observations") as batch:
        batch.alter_column(
            "execution_id", existing_type=sa.String(length=36), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("federation_delegation_observations") as batch:
        batch.alter_column(
            "execution_id", existing_type=sa.String(length=36), nullable=False
        )
    with op.batch_alter_table("federation_delegations") as batch:
        batch.drop_column("created_at")
