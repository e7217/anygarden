"""Revision provenance, immutable turn scope, and exact stop receipts."""

import sqlalchemy as sa
from alembic import op
from anygarden.db.types import UtcDateTime

revision = "089_execution_mutations"
down_revision = "088_agent_room_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_turns") as batch:
        batch.add_column(sa.Column("execution_id", sa.String(36)))
        batch.add_column(sa.Column("execution_input_revision", sa.Integer()))
        batch.create_foreign_key("fk_turn_execution", "project_executions", ["execution_id"], ["id"], ondelete="SET NULL")
    op.add_column("agent_turn_attempts", sa.Column("local_execution_id", sa.String(36)))
    with op.batch_alter_table("execution_input_revisions") as batch:
        for name in ("source_message_id", "actor_user_id", "root_task_id"):
            batch.add_column(sa.Column(name, sa.String(36)))
        batch.add_column(sa.Column("reason", sa.Text()))
        batch.add_column(sa.Column("change_scope", sa.String(24), nullable=False, server_default="all"))
        for name, target in (("source_message_id", "messages"), ("actor_user_id", "users"), ("root_task_id", "tasks")):
            batch.create_foreign_key(f"fk_input_revision_{name}", target, [name], ["id"], ondelete="SET NULL")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("uq_execution_task_delegation", type_="unique")
        batch.create_unique_constraint("uq_execution_task_delegation", ["execution_id", "input_revision", "delegation_key"])
    op.execute("""UPDATE agent_turns SET
        execution_id=(SELECT execution_id FROM tasks WHERE tasks.id=agent_turns.task_id),
        execution_input_revision=(SELECT input_revision FROM tasks WHERE tasks.id=agent_turns.task_id)
        WHERE task_id IN (SELECT id FROM tasks WHERE execution_id IS NOT NULL AND input_revision IS NOT NULL)""")
    op.execute("""UPDATE execution_input_revisions SET
        source_message_id=(SELECT source_message_id FROM project_executions WHERE project_executions.id=execution_id),
        actor_user_id=(SELECT owner_user_id FROM project_executions WHERE project_executions.id=execution_id),
        root_task_id=(SELECT root_task_id FROM project_executions WHERE project_executions.id=execution_id)
        WHERE revision=1""")
    op.create_table(
        "execution_mutations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("project_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("previous_input_revision", sa.Integer(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("expected_state_revision", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("root_task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_at", UtcDateTime(), nullable=False),
        sa.Column("completed_at", UtcDateTime()),
        sa.UniqueConstraint("execution_id", "operation_id", name="uq_execution_mutation_operation"),
    )
    op.create_index("ix_execution_mutations_phase", "execution_mutations", ["execution_id", "phase"])
    op.create_table(
        "execution_stops",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mutation_id", sa.String(36), sa.ForeignKey("execution_mutations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("project_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("agent_turns.request_id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("agent_turn_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participant_id", sa.String(36), sa.ForeignKey("participants.id", ondelete="SET NULL")),
        sa.Column("local_execution_id", sa.String(36)),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("delivery_count", sa.Integer(), nullable=False),
        sa.Column("available_at", UtcDateTime(), nullable=False),
        sa.Column("deadline_at", UtcDateTime(), nullable=False),
        sa.Column("requested_at", UtcDateTime(), nullable=False),
        sa.Column("delivered_at", UtcDateTime()),
        sa.Column("confirmed_at", UtcDateTime()),
        sa.Column("receipt", sa.JSON()),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("attempt_id", name="uq_execution_stop_attempt"),
    )
    op.create_index("ix_execution_stops_delivery", "execution_stops", ["status", "available_at"])
    op.create_index("ix_execution_stops_execution", "execution_stops", ["execution_id", "input_revision"])


def downgrade() -> None:
    op.drop_table("execution_stops")
    op.drop_table("execution_mutations")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("uq_execution_task_delegation", type_="unique")
        # Multiple revisions can reuse a key; a downgrade cannot erase that history.
        batch.create_unique_constraint("uq_execution_task_delegation", ["execution_id", "delegation_key"])
    with op.batch_alter_table("execution_input_revisions") as batch:
        for name in ("source_message_id", "actor_user_id", "root_task_id"):
            batch.drop_constraint(f"fk_input_revision_{name}", type_="foreignkey")
        for name in ("source_message_id", "actor_user_id", "root_task_id", "reason", "change_scope"):
            batch.drop_column(name)
    op.drop_column("agent_turn_attempts", "local_execution_id")
    with op.batch_alter_table("agent_turns") as batch:
        batch.drop_constraint("fk_turn_execution", type_="foreignkey")
        batch.drop_column("execution_input_revision")
        batch.drop_column("execution_id")
