"""Preserve successful prerequisite inputs for task handoffs."""

import sqlalchemy as sa
from alembic import op

revision = "083_task_dependency_results"
down_revision = "082_project_created_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("dependency_results", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "dependency_results")
