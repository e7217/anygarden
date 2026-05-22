"""Add machine_activity_logs table.

Revision ID: 011
Revises: 010
Create Date: 2026-04-14

Rationale
---------
Track machine lifecycle events (online, offline, drain) separately
from agent activity logs. Heartbeats are excluded to keep volume low.
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"


def upgrade() -> None:
    op.create_table(
        "machine_activity_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("machine_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_machine_activity_logs_machine_ts",
        "machine_activity_logs",
        ["machine_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_table("machine_activity_logs")
