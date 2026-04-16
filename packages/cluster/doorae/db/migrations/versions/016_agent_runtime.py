"""Add agents.runtime column.

Revision ID: 016
Revises: 015
Create Date: 2026-04-16

Rationale
---------
Issue #73 — the machine daemon gains a new spawn arm for a
TypeScript runtime (``doorae-agent-ts``) alongside the existing
Python ``doorae-agent``. Each Agent row picks one; the column
selects which runtime the machine spawns on the target host.

``'python'`` (the default) preserves every pre-#73 agent's behaviour
without a data migration. ``'typescript'`` activates the new
Node/TS path for engines whose first-party SDK is TS-native
(Claude Code, future Codex/Gemini CLI).

Kept as a short ``String(20)`` — we only expect a handful of valid
values (enum-like) but avoid SQLAlchemy ``Enum`` to sidestep the
alembic-on-sqlite type migration gotcha.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "016"
down_revision: str = "015"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ``batch_alter_table`` for SQLite compatibility — the table
    # recreation dance the batch helper performs is a no-op on
    # Postgres but essential on SQLite.
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column(
                "runtime",
                sa.String(20),
                nullable=False,
                server_default="python",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("runtime")
