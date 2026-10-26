"""Metric-driven campaigns (#930).

A campaign keeps opening cycle Tasks until its target is confirmed, its
deadline passes or its budget runs out. ``campaigns`` holds the purpose,
judgement criteria and loop state; ``campaign_attempts`` one row per
cycle; ``campaign_events`` the append-only history. ``tasks.campaign_id``
marks a campaign's cycle Task.

Revision ID: 097_campaigns
Revises: 096_machine_transfers
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "097_campaigns"
down_revision = "096_machine_transfers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("metric", sa.JSON(), nullable=False),
        sa.Column("evaluator", sa.JSON(), nullable=False),
        sa.Column("evaluator_pins", sa.JSON(), nullable=True),
        sa.Column("trusted_eval", sa.String(16), nullable=False, server_default="none"),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("deadline_at", UtcDateTime(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("stop_reason", sa.String(64), nullable=True),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("cycles_run", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("best_attempt_id", sa.String(36), nullable=True),
        sa.Column("final_attempt_id", sa.String(36), nullable=True),
        sa.Column("next_cycle_at", UtcDateTime(), nullable=True),
        sa.Column("wake_reason", sa.String(500), nullable=True),
        sa.Column("cycles_since_improvement", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_eval_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retro_due", sa.String(32), nullable=True),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
    )
    op.create_index("ix_campaigns_status_next_cycle", "campaigns", ["status", "next_cycle_at"])
    op.create_index("ix_campaigns_room", "campaigns", ["room_id"])

    op.create_table(
        "campaign_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="cycle"),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "execution_id", sa.String(36),
            sa.ForeignKey("project_executions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("opened_at", UtcDateTime(), nullable=False),
        sa.Column("closed_at", UtcDateTime(), nullable=True),
        sa.Column("agent_outcome", sa.String(16), nullable=True),
        sa.Column("hypothesis", sa.Text(), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("artifact_ref", sa.String(1024), nullable=True),
        sa.Column("lessons", sa.Text(), nullable=True),
        sa.Column("recorded_at", UtcDateTime(), nullable=True),
        sa.Column("wake_at", UtcDateTime(), nullable=True),
        sa.Column("wake_reason", sa.String(500), nullable=True),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("unblock_hint", sa.Text(), nullable=True),
        sa.Column("eval_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("eval_job_id", sa.String(64), nullable=True),
        sa.Column("eval_deadline_at", UtcDateTime(), nullable=True),
        sa.Column("dev_score", sa.Float(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("valid", sa.Boolean(), nullable=True),
        sa.Column("eval_excerpt", sa.Text(), nullable=True),
        sa.Column("eval_started_at", UtcDateTime(), nullable=True),
        sa.Column("eval_finished_at", UtcDateTime(), nullable=True),
        sa.Column("trusted_score", sa.Float(), nullable=True),
        sa.Column("trusted_at", UtcDateTime(), nullable=True),
        sa.Column("tokens", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.UniqueConstraint("campaign_id", "cycle_no", name="uq_campaign_attempt_cycle"),
    )
    op.create_index("ix_campaign_attempts_task", "campaign_attempts", ["task_id"])
    op.create_index("ix_campaign_attempts_execution", "campaign_attempts", ["execution_id"])
    op.create_index("ix_campaign_attempts_eval_job", "campaign_attempts", ["eval_job_id"])

    op.create_table(
        "campaign_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("actor", sa.String(64), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.UniqueConstraint("event_key", name="uq_campaign_events_key"),
    )
    op.create_index("ix_campaign_events_campaign", "campaign_events", ["campaign_id", "created_at"])

    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("campaign_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_campaign", "campaigns", ["campaign_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_tasks_campaign", ["campaign_id"])


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_campaign")
        batch.drop_constraint("fk_tasks_campaign", type_="foreignkey")
        batch.drop_column("campaign_id")
    op.drop_index("ix_campaign_events_campaign", table_name="campaign_events")
    op.drop_table("campaign_events")
    op.drop_index("ix_campaign_attempts_eval_job", table_name="campaign_attempts")
    op.drop_index("ix_campaign_attempts_execution", table_name="campaign_attempts")
    op.drop_index("ix_campaign_attempts_task", table_name="campaign_attempts")
    op.drop_table("campaign_attempts")
    op.drop_index("ix_campaigns_room", table_name="campaigns")
    op.drop_index("ix_campaigns_status_next_cycle", table_name="campaigns")
    op.drop_table("campaigns")
