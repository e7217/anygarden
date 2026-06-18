"""task_blockers dependency relation.

Revision ID: 046
Revises: 045
Create Date: 2026-06-18

Issue #459 (reliability Wave 2c) — adds the ``task_blockers`` relation so a
blocked Task records *what* it is waiting on. When a blocker Task reaches a
terminal status the resolve-wake hook clears the satisfied edge and, once
every blocker of the dependent is terminal, returns the dependent to
``todo`` and re-injects its assignment mention.

``task_blockers`` is a pure relation table:

- Composite PK ``(task_id, blocked_by_task_id)`` — the edge identity, so a
  duplicate add raises IntegrityError (the handler treats that as
  idempotent success).
- Both columns FK ``tasks.id`` with ``ON DELETE CASCADE`` so deleting either
  endpoint task tears down its blocker edges (no dangling edges for the
  cycle-guard walk to trip on). Mirrors the rooms→tasks cascade.
- ``ix_task_blockers_blocked_by`` accelerates the resolve-wake reverse
  lookup (``WHERE blocked_by_task_id = :just_completed``), which the
  leading-``task_id`` PK index cannot serve.

A fresh DB has no blocker rows and the MCP tools are agent-driven, so
merging this migration cannot change runtime behaviour on its own.

Downgrade drops the index then the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "046"
down_revision: str = "045"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "task_blockers",
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("blocked_by_task_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["blocked_by_task_id"], ["tasks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint(
            "task_id", "blocked_by_task_id", name="pk_task_blockers"
        ),
    )
    op.create_index(
        "ix_task_blockers_blocked_by",
        "task_blockers",
        ["blocked_by_task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_blockers_blocked_by",
        table_name="task_blockers",
    )
    op.drop_table("task_blockers")
