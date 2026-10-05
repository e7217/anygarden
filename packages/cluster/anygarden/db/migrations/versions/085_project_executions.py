"""Persist project execution lineage, input snapshots and accepted results."""

import sqlalchemy as sa
from alembic import op
from anygarden.db.types import UtcDateTime

revision = "085_project_executions"
down_revision = "084_task_run_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operating_room_id",
            sa.String(36),
            sa.ForeignKey("rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "source_message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "root_task_id",
            sa.String(36),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "goal_id",
            sa.String(36),
            sa.ForeignKey("agent_goals.id", ondelete="SET NULL"),
        ),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("completion_criteria", sa.JSON(), nullable=False),
        sa.Column("allowed_actions", sa.JSON(), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="planning"),
        sa.Column(
            "plan_sealed", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("required_task_ids", sa.JSON(), nullable=False),
        sa.Column(
            "requires_qa", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("delegation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("usage_summary", sa.JSON()),
        sa.Column("deadline_at", UtcDateTime()),
        sa.Column(
            "final_report_message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column("finished_at", UtcDateTime()),
        sa.UniqueConstraint("source_message_id", name="uq_execution_source_message"),
    )
    op.create_index(
        "ix_executions_room_status",
        "project_executions",
        ["operating_room_id", "status"],
    )
    op.create_table(
        "execution_input_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("project_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("user_constraints", sa.Text(), nullable=False),
        sa.Column("input_files", sa.JSON(), nullable=False),
        sa.Column("completion_criteria", sa.JSON(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.UniqueConstraint(
            "execution_id", "revision", name="uq_execution_input_revision"
        ),
    )
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("execution_id", sa.String(36)))
        batch.add_column(sa.Column("parent_task_id", sa.String(36)))
        batch.add_column(sa.Column("input_revision", sa.Integer()))
        batch.add_column(
            sa.Column(
                "delegation_depth", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch.add_column(sa.Column("delegation_key", sa.String(160)))
        batch.add_column(sa.Column("role", sa.String(48)))
        batch.add_column(
            sa.Column(
                "required_for_execution",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(sa.Column("qa_target_task_id", sa.String(36)))
        batch.add_column(sa.Column("qa_target_result_version", sa.Integer()))
        batch.add_column(
            sa.Column(
                "result_version", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch.create_foreign_key(
            "fk_tasks_execution",
            "project_executions",
            ["execution_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_tasks_parent_task",
            "tasks",
            ["parent_task_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_tasks_qa_target",
            "tasks",
            ["qa_target_task_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_execution_task_delegation", ["execution_id", "delegation_key"]
        )
        batch.create_index(
            "ix_tasks_execution_parent", ["execution_id", "parent_task_id"]
        )
    op.create_table(
        "task_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("project_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("agent_turn_attempts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "producer_agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
        ),
        sa.Column("result_markdown", sa.Text(), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("artifacts", sa.JSON(), nullable=False),
        sa.Column("verification", sa.JSON()),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "version", name="uq_task_result_version"),
        sa.UniqueConstraint("attempt_id", "task_id", name="uq_task_result_attempt"),
    )
    op.create_index(
        "ix_task_results_execution", "task_results", ["execution_id", "created_at"]
    )
    op.create_table(
        "project_execution_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("project_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="SET NULL")
        ),
        sa.Column("event_key", sa.String(240), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.UniqueConstraint("event_key", name="uq_execution_event_key"),
    )
    op.create_index(
        "ix_execution_events_execution",
        "project_execution_events",
        ["execution_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("project_execution_events")
    op.drop_table("task_results")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_execution_parent")
        batch.drop_constraint("uq_execution_task_delegation", type_="unique")
        for name in (
            "fk_tasks_qa_target",
            "fk_tasks_parent_task",
            "fk_tasks_execution",
        ):
            batch.drop_constraint(name, type_="foreignkey")
        for name in (
            "result_version",
            "qa_target_result_version",
            "qa_target_task_id",
            "required_for_execution",
            "role",
            "delegation_key",
            "delegation_depth",
            "input_revision",
            "parent_task_id",
            "execution_id",
        ):
            batch.drop_column(name)
    op.drop_table("execution_input_revisions")
    op.drop_table("project_executions")
