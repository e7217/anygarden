"""Add tasks.assigned_at + backfill assigned rows.

Revision ID: 038
Revises: 037
Create Date: 2026-04-28

Rationale
---------
Issue #314 — the goal-scheduler sweeper needs a ``status='todo'`` →
``failed`` transition once an assignee has been ignoring the task for
``TASK_PICKUP_TIMEOUT_SECONDS``. ``created_at`` is the wrong clock for
this: a manual to-do can sit unassigned for days before someone
attaches an assignee, and we don't want that delay to count against
the new assignee. ``assigned_at`` snapshots the moment an assignee was
actually attached (or last reassigned).

Backfill — pre-existing rows with ``assignee_participant_id IS NOT
NULL`` get ``assigned_at = created_at``. The estimate is wrong by
exactly the unassigned dwell time (often zero), and any rows that are
*already* past the pickup timeout will simply be swept on the next
``GoalScheduler._tick`` — which is the documented intent for the
backlog of stuck tasks (#314 plan §3.3).

``downgrade`` just drops the column; backfilled values are not
re-derivable but were always best-effort approximations.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "038"
down_revision: str = "037"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "assigned_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    # Backfill: assigned rows take ``assigned_at = created_at``. Rows
    # without an assignee stay NULL — the sweeper's ``IS NOT NULL``
    # guard skips them. Done in one statement for atomicity.
    op.execute(
        """
        UPDATE tasks
           SET assigned_at = created_at
         WHERE assignee_participant_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("tasks", "assigned_at")
