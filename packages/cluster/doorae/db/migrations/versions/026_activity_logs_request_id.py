"""Add activity_logs.request_id column + index.

Revision ID: 026
Revises: 025
Create Date: 2026-04-20

Rationale
---------
Issue #204 — explicit request lifecycle for agent observability.

Adds a nullable ``request_id`` column to ``activity_logs`` so every
lifecycle event (``handler_started``, ``engine_call_started``,
``engine_call_finished``, ``handler_finished``, ``handler_orphaned``)
plus the existing cluster-side events (``message_received``,
``response_sent``) can be joined under one identifier. Legacy rows
(including the now-removed ``processing_started`` bursts) stay as
``request_id=NULL`` and remain queryable via the existing
``(agent_id, timestamp)`` index.

The extra index on ``request_id`` alone enables O(log n) lookup of
"all events for request X" — the dominant read pattern for the
redesigned Activity dialog.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "026"
down_revision: str = "025"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("activity_logs") as batch:
        batch.add_column(sa.Column("request_id", sa.String(36), nullable=True))
    op.create_index(
        "ix_activity_logs_request",
        "activity_logs",
        ["request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_logs_request", table_name="activity_logs")
    with op.batch_alter_table("activity_logs") as batch:
        batch.drop_column("request_id")
