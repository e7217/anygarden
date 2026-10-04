"""Keep schedule provenance and consumed run identities for silent successes."""

import sqlalchemy as sa
from alembic import op

revision = "084_task_run_ledger"
down_revision = "083_task_dependency_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("schedule_context", sa.JSON(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column(
            "is_silent", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "goal_completion_applied",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute(
        "UPDATE tasks SET goal_completion_applied = 1 "
        "WHERE goal_id IS NOT NULL AND status IN ('done', 'failed')"
    )


def downgrade() -> None:
    op.drop_column("tasks", "goal_completion_applied")
    op.drop_column("tasks", "is_silent")
    op.drop_column("tasks", "schedule_context")
