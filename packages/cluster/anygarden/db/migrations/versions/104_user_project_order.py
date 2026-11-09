"""Per-user sidebar project order (#995).

``user_project_orders`` holds each user's own position for a project.
A project without a row keeps its creation-time position after the
ordered ones.

Revision ID: 104_user_project_order
Revises: 103_user_api_keys
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "104_user_project_order"
down_revision = "103_user_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_project_orders",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "project_id"),
    )


def downgrade() -> None:
    op.drop_table("user_project_orders")
