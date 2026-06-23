"""Add agents.turn_timeout_sec column.

Revision ID: 049
Revises: 048
Create Date: 2026-06-23

Rationale
---------
Issue #493 — per-agent turn timeout (seconds). The machine forwards it into
the agent process env (``ANYGARDEN_AGENT_TURN_TIMEOUT_SEC``) and the engine
adapters resolve it via ``anygarden_agent.integrations._turn_timeout`` (#492),
deriving the WS ping / supervisor deadlines from it.

The column is nullable with no server default so existing rows land as NULL
and the adapters interpret NULL as "use the global env / hardcoded default" —
identical to the current behaviour. This avoids a backfill migration and keeps
rollback symmetric, mirroring 038 (permission_level).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "049"
down_revision: str = "048"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("turn_timeout_sec", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("turn_timeout_sec")
