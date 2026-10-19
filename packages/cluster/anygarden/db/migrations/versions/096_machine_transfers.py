"""One-time tickets for work-file transfers over HTTP (#914).

Revision ID: 096_machine_transfers
Revises: 095_agent_instruction_revisions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "096_machine_transfers"
down_revision = "095_agent_instruction_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machine_transfers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("machine_id", sa.String(36), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False, server_default=""),
        sa.Column("max_bytes", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="issued"),
        sa.Column("spool_name", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
    )
    op.create_index("ix_machine_transfers_expires", "machine_transfers", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_machine_transfers_expires", table_name="machine_transfers")
    op.drop_table("machine_transfers")
