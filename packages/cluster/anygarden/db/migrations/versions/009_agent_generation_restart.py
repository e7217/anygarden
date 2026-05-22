"""Add agents.generation, max_restarts, restart_window_seconds columns.

Revision ID: 009
Revises: 008
Create Date: 2026-04-13

Rationale
---------
Declarative desired-state model: generation tracks config version so
machines know when to restart. max_restarts and restart_window_seconds
configure per-agent crash budget for local restart rate limiting.
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"


def upgrade() -> None:
    op.add_column("agents", sa.Column("generation", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agents", sa.Column("max_restarts", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("agents", sa.Column("restart_window_seconds", sa.Integer(), nullable=False, server_default="300"))


def downgrade() -> None:
    op.drop_column("agents", "restart_window_seconds")
    op.drop_column("agents", "max_restarts")
    op.drop_column("agents", "generation")
