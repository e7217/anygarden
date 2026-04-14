"""Add agents.model column.

Revision ID: 012
Revises: 011
Create Date: 2026-04-15

Rationale
---------
Let each agent pin a specific engine model (e.g. ``gpt-5.4-mini`` vs
``gpt-5.4``) rather than inheriting the adapter's built-in default.
Nullable on purpose — existing agents continue to use the adapter
default until an admin explicitly chooses one. See issue #4.
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"


def upgrade() -> None:
    op.add_column("agents", sa.Column("model", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "model")
