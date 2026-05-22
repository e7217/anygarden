"""Add agents.permission_level column.

Revision ID: 038
Revises: 037
Create Date: 2026-04-28

Rationale
---------
Issue #309 — semantic 3-tier permission abstraction
(``restricted | standard | trusted``) per agent. Each engine adapter
translates the tier into its native dial (codex ``sandbox`` +
``approval_policy``; gemini-cli ``--approval-mode``; claude-code
``permissions.allow`` whitelist).

The column is nullable with no server default so existing rows
land as NULL and the adapters interpret NULL as ``standard`` —
identical to the current hardcoded behaviour. This avoids a
backfill migration and keeps rollback symmetric.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "038"
down_revision: str = "037"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column("permission_level", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("permission_level")
