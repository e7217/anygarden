"""Token budget incidents + agents.pause_reason.

Revision ID: 045
Revises: 044
Create Date: 2026-06-18

Issue #455 (reliability Wave 2a) — adds the active-stop / incident half
on top of Wave 1d's invocation-block gate.

1. ``agents.pause_reason`` — nullable String(32), no server default, so
   every pre-#455 row lands as NULL ("not paused for a special reason").
   The active-stop path sets it to ``'budget'`` when an AGENT-scope
   hard-stop ceiling is breached; admin resume clears it back to NULL.
   Nullable + no default keeps rollback symmetric and needs no backfill.

2. ``token_budget_incidents`` — one recorded breach per (policy, window,
   threshold). ``hard_stop_enabled`` policies are still the only thing
   that drives writes here, and there are none on a fresh DB, so merging
   this migration cannot change runtime behaviour (the Wave 1d
   default-OFF invariant carries forward). No FK on ``policy_id`` (a
   deleted policy must not erase the breach audit trail). Indexed on
   (policy_id, status) for the dedup lookup + resume sweep, and on
   (scope_type, scope_id) for the admin scope grouping.

Downgrade reverses both in reverse order.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "045"
down_revision: str = "044"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column("pause_reason", sa.String(length=32), nullable=True)
        )

    op.create_table(
        "token_budget_incidents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("policy_id", sa.String(36), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("threshold_type", sa.String(8), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="open",
        ),
        sa.Column("observed_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_token_budget_incidents_policy_status",
        "token_budget_incidents",
        ["policy_id", "status"],
    )
    op.create_index(
        "ix_token_budget_incidents_scope",
        "token_budget_incidents",
        ["scope_type", "scope_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_token_budget_incidents_scope",
        table_name="token_budget_incidents",
    )
    op.drop_index(
        "ix_token_budget_incidents_policy_status",
        table_name="token_budget_incidents",
    )
    op.drop_table("token_budget_incidents")

    with op.batch_alter_table("agents") as batch:
        batch.drop_column("pause_reason")
