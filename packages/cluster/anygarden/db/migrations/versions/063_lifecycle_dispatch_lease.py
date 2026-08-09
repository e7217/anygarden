"""Durable lifecycle dispatch ownership and legacy report epoch fencing.

Revision ID: 063
Revises: 062
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision: str = "063"
down_revision: str = "062"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column("lifecycle_lease_token", sa.String(64), nullable=True)
        )
        batch.add_column(
            sa.Column("lifecycle_lease_expires_at", UtcDateTime(), nullable=True)
        )
        batch.add_column(
            sa.Column("lifecycle_delivery_state", sa.String(24), nullable=True)
        )
        batch.add_column(
            sa.Column("legacy_report_generation", sa.Integer(), nullable=True)
        )
        batch.create_index(
            "ix_agents_lifecycle_delivery_lease",
            ["lifecycle_delivery_state", "lifecycle_lease_expires_at"],
        )

    # Preserve mixed-rollout compatibility for the process generation that
    # was already placed when this migration landed. The next generation
    # advance intentionally leaves this epoch unchanged, fencing unversioned
    # reports from that earlier process.
    op.execute(
        sa.text(
            "UPDATE agents SET legacy_report_generation = generation "
            "WHERE placed_on_machine_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_index("ix_agents_lifecycle_delivery_lease")
        batch.drop_column("legacy_report_generation")
        batch.drop_column("lifecycle_delivery_state")
        batch.drop_column("lifecycle_lease_expires_at")
        batch.drop_column("lifecycle_lease_token")
