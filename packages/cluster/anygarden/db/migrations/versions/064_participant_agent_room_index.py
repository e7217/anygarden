"""Add participant query index for lifecycle batch lookups.

Revision ID: 064
Revises: 063
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op

revision: str = "064"
down_revision: str = "063"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "ix_participants_agent_room",
        "participants",
        ["agent_id", "room_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_participants_agent_room", table_name="participants")
