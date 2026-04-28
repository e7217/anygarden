"""Add agent_goals + extend tasks for the autonomous responsibility system.

Revision ID: 037
Revises: 036
Create Date: 2026-04-28

Rationale
---------
Issue #302 (Phase 2) — the autonomous responsibility MVP. Two
intertwined changes ship together because the new ``agent_goals``
table is FK-referenced from extended ``tasks`` columns:

1. ``agent_goals`` (new): the *definition* of a recurring duty an
   agent owns. Carries trigger config (cron / interval / manual),
   spec (markdown injected at every fire), report room, materialize
   policy, and bookkeeping (``consecutive_failures``, ``next_run_at``,
   ``last_run_at``).

2. ``tasks`` (extended): becomes the single ledger of "things an
   agent did". Manual rows keep ``goal_id IS NULL`` and ``triggered_by
   = 'manual'``; scheduler-fired rows carry the link plus a snapshot
   of ``goal.spec`` + execution metadata (started/finished, tokens,
   result_markdown, error, is_interesting).

The unified Task model (vs. a parallel ``goal_runs`` table) was the
explicit decision in plan-302 §3.2 D11/D12. It lets TaskPanel /
TasksSection / mark_task_status MCP / WS doorae:task:updated all work
unchanged on Goal-derived rows; the ``goal_id`` link is the only thing
the server has to teach UIs about.

All new ``tasks`` columns are nullable + sensibly defaulted so the
migration leaves pre-existing manual rows untouched. ``downgrade``
drops them in the reverse order so a roll-back never leaves an FK
pointing at a dropped table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: str = "036"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. agent_goals (new) ───────────────────────────────────────
    op.create_table(
        "agent_goals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "assignee_agent_id", sa.String(length=36), nullable=False
        ),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column(
            "report_room_id", sa.String(length=36), nullable=True
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("trigger_config", sa.JSON(), nullable=False),
        sa.Column(
            "materialize",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'interesting_only'"),
        ),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "next_run_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_run_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        # Removing an agent or its owner removes their goals — the
        # responsibility cannot persist without an executor.
        sa.ForeignKeyConstraint(
            ["assignee_agent_id"], ["agents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], ondelete="CASCADE"
        ),
        # Room deletion downgrades to a silent goal rather than
        # cascading the goal away. Users can re-point a goal to a
        # different room via PATCH.
        sa.ForeignKeyConstraint(
            ["report_room_id"], ["rooms.id"], ondelete="SET NULL"
        ),
    )
    # Scheduler hot path — "every active goal whose next_run has
    # elapsed". The composite serves the predicate
    # ``WHERE status='active' AND next_run_at <= now()``.
    op.create_index(
        "ix_agent_goals_status_next_run",
        "agent_goals",
        ["status", "next_run_at"],
    )
    op.create_index(
        "ix_agent_goals_assignee", "agent_goals", ["assignee_agent_id"]
    )
    op.create_index(
        "ix_agent_goals_report_room", "agent_goals", ["report_room_id"]
    )

    # ── 2. tasks (extend) ──────────────────────────────────────────
    # All new columns nullable + defaulted so existing rows survive
    # the migration without backfill. ``triggered_by`` defaults to
    # 'manual' since pre-#302 every row was user-created.
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column("goal_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "triggered_by",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'manual'"),
            )
        )
        batch.add_column(sa.Column("spec", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "started_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch.add_column(
            sa.Column(
                "finished_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch.add_column(
            sa.Column("agent_session_id", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("tokens_used", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("result_markdown", sa.Text(), nullable=True)
        )
        batch.add_column(sa.Column("error", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "is_interesting",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
        batch.create_foreign_key(
            "fk_tasks_goal_id",
            "agent_goals",
            ["goal_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Goal detail "recent runs" panel: ``WHERE goal_id = ? ORDER BY
    # created_at DESC LIMIT N``. Composite keeps that scan cheap when
    # a high-frequency goal accumulates thousands of rows.
    op.create_index(
        "ix_tasks_goal_created", "tasks", ["goal_id", "created_at"]
    )


def downgrade() -> None:
    # Reverse order: drop FK + columns first, then the table they
    # reference. Otherwise Postgres refuses to drop ``agent_goals``
    # while ``tasks.goal_id`` still references it.
    op.drop_index("ix_tasks_goal_created", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_goal_id", type_="foreignkey")
        batch.drop_column("is_interesting")
        batch.drop_column("error")
        batch.drop_column("result_markdown")
        batch.drop_column("tokens_used")
        batch.drop_column("agent_session_id")
        batch.drop_column("finished_at")
        batch.drop_column("started_at")
        batch.drop_column("spec")
        batch.drop_column("triggered_by")
        batch.drop_column("goal_id")

    op.drop_index(
        "ix_agent_goals_report_room", table_name="agent_goals"
    )
    op.drop_index(
        "ix_agent_goals_assignee", table_name="agent_goals"
    )
    op.drop_index(
        "ix_agent_goals_status_next_run", table_name="agent_goals"
    )
    op.drop_table("agent_goals")
