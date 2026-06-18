"""Promote activity_logs.outcome / engine to first-class indexed columns.

Revision ID: 042
Revises: 041
Create Date: 2026-06-18

Issue #447 — the activity-log reaper and outcome-filtered timelines query
by turn outcome (and the engine that ran it). These previously lived only
inside the ``details`` JSON, so those queries meant a full scan +
json_extract. This adds two nullable indexed columns mirroring #427's
``room_id`` promotion.

Unlike #427 there is no backfill: legacy ``details`` rows carry no
consistent outcome/engine signal, so the columns are forward-only and
populated from new LifecycleFrames going forward.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "042"
down_revision: str = "041"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "activity_logs",
        sa.Column("outcome", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "activity_logs",
        sa.Column("engine", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_activity_logs_outcome_ts",
        "activity_logs",
        ["outcome", "timestamp"],
    )
    op.create_index(
        "ix_activity_logs_room_outcome",
        "activity_logs",
        ["room_id", "outcome"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_logs_room_outcome", table_name="activity_logs")
    op.drop_index("ix_activity_logs_outcome_ts", table_name="activity_logs")
    op.drop_column("activity_logs", "engine")
    op.drop_column("activity_logs", "outcome")
