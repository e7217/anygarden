"""Add agents.memory_md column.

Revision ID: 030
Revises: 029
Create Date: 2026-04-22

Rationale
---------
Issue #237 — cross-engine long-term memory lives in a per-agent
``memory/notes.md`` file on the hosting machine. The DB holds the
"last-known snapshot" so an agent's memory survives restart /
machine move / row rebuild. Null default: new / existing agents
start with empty memory until their first write.

DB ↔ file sync direction (see plan §3.2 decision 4): file is the
runtime truth, DB is the snapshot. Spawner materializes DB → file
at start; machine heartbeat flushes file → DB on change.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "030"
down_revision: str = "029"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column(
                "memory_md",
                sa.Text(),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("memory_md")
