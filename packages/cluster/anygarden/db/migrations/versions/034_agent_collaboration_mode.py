"""Add agents.collaboration_mode column.

Revision ID: 034
Revises: 033
Create Date: 2026-04-27

Rationale
---------
Issue #279 — give each agent a collaboration policy axis ("solo" vs
"collaborative") so a roster can express "this agent always tries to
delegate via peer mentions" without piling another enum onto the
``rooms`` table. ``collaboration_mode='collaborative'`` causes the
agent SDK to append a peer-mention usage hint after the room roster
suffix at LLM prompt assembly time; ``solo`` (default) preserves the
pre-#279 behaviour byte-for-byte.

NOT NULL with ``server_default 'solo'`` so existing rows are
auto-populated without a backfill script.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "034"
down_revision: str = "033"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column(
                "collaboration_mode",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'solo'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("collaboration_mode")
