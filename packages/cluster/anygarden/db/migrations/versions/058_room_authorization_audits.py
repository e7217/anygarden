"""Add durable global-admin room authorization bypass audits.

Revision ID: 058
Revises: 057
Create Date: 2026-08-05

The Phase 1 policy grants global administrators operational access without a
Participant row, but requires every use of that bypass to remain auditable.
Actor and room IDs intentionally are not foreign keys so user/room deletion
cannot erase the historical boundary crossing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "058"
down_revision: str = "057"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "room_authorization_audits",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=False),
        sa.Column("room_id", sa.String(36), nullable=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_room_authorization_audits_actor_at",
        "room_authorization_audits",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_room_authorization_audits_room_at",
        "room_authorization_audits",
        ["room_id", "created_at"],
    )
    op.create_index(
        "ix_room_authorization_audits_scope_at",
        "room_authorization_audits",
        ["scope", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_room_authorization_audits_scope_at",
        table_name="room_authorization_audits",
    )
    op.drop_index(
        "ix_room_authorization_audits_room_at",
        table_name="room_authorization_audits",
    )
    op.drop_index(
        "ix_room_authorization_audits_actor_at",
        table_name="room_authorization_audits",
    )
    op.drop_table("room_authorization_audits")
