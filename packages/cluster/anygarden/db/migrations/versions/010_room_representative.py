"""Add rooms.representative_agent_id column.

Revision ID: 010
Revises: 009
Create Date: 2026-04-13

Rationale
---------
Each room can have a representative agent. When a user mentions a room
via #room syntax, the representative agent collects opinions from other
agents in that room and delivers a synthesized response.
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"


def upgrade() -> None:
    op.add_column(
        "rooms",
        sa.Column("representative_agent_id", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rooms", "representative_agent_id")
