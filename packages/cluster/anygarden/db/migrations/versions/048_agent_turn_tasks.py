"""agent_turn_tasks request_id↔task correlation.

Revision ID: 048
Revises: 047
Create Date: 2026-06-19

Issue #463 (reliability Wave 2 — lifecycle→Task re-dispatch bridge) — adds
the ``agent_turn_tasks`` table that correlates a minted ``request_id`` with
the Task an *assignment-originated* turn was woken to work on.

``inject_task_assignment_message`` now mints a server-side ``request_id``,
stamps it onto the injected ``[TASK]`` message metadata (the same key the
live user-send path uses, so the assignee agent threads it onto its
lifecycle frames), and writes one row here. When the assignee's
``handler_finished`` frame returns a terminal non-ok outcome the WS handler
looks the turn up here and re-dispatches the still-unresolved Task once
(bounded by the carried ``redispatch_count``). Live (user-send) turns never
write a row here, so the bridge leaves them untouched.

``agent_turn_tasks`` is a small correlation table:

- ``request_id`` is the PK — the only access pattern is a point lookup by
  ``request_id`` from the lifecycle receive path, so no extra index.
- ``task_id`` FK ``tasks.id`` with ``ON DELETE CASCADE`` so deleting the
  Task tears down its turn-correlation rows (mirrors rooms→tasks /
  task_blockers cascades).
- ``redispatch_count`` (default 0) is carried + incremented across the
  re-dispatch chain so the flip-loop is bounded.

A fresh DB has no turn rows and the correlation is only written by the
injection path, so merging this migration cannot change runtime behaviour
on its own.

Downgrade drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "048"
down_revision: str = "047"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "agent_turn_tasks",
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column(
            "redispatch_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("request_id", name="pk_agent_turn_tasks"),
    )


def downgrade() -> None:
    op.drop_table("agent_turn_tasks")
