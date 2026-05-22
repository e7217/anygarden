"""Add tasks assignee index and rooms.allow_human_assignment.

Revision ID: 032
Revises: 031
Create Date: 2026-04-25

Rationale
---------
Issue #266 — task assignment + auto-execution. Two schema additions:

- ``ix_tasks_assignee_status`` — accelerates the per-agent task
  aggregation query in ``GET /api/v1/agents/{id}/tasks`` which joins
  ``tasks`` to ``participants`` on ``assignee_participant_id``.
  Existing ``ix_tasks_room_status`` covers the room-scoped 1차 view;
  the assignee index is the dual for the 에이전트-scoped 2차 view.
- ``rooms.allow_human_assignment`` — opt-in toggle controlling whether
  human participants appear in the task assignee dropdown. Default
  ``False`` so existing rooms behave like before (agent-only target).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: str = "031"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "ix_tasks_assignee_status",
        "tasks",
        ["assignee_participant_id", "status"],
    )

    with op.batch_alter_table("rooms") as batch:
        batch.add_column(
            sa.Column(
                "allow_human_assignment",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.drop_column("allow_human_assignment")

    op.drop_index("ix_tasks_assignee_status", table_name="tasks")
