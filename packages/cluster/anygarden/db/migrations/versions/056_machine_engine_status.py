"""Add machine_engine_status: per-machine per-engine latest version + update state.

Revision ID: 056
Revises: 055
Create Date: 2026-07-24

Rationale
---------
Issue #553 — engine CLI lifecycle (latest-version check + server-driven
update). The state must live in its own table rather than on ``machine_engines``
because ``_handle_register`` delete+recreates that table on every reconnect —
the very re-register that would confirm an update also wipes the row. This
table is keyed uniquely by ``(machine_id, engine)`` and is independent of the
detection sync:

- ``latest_version`` (String(64), nullable) — channel-normalized latest from
  the registry, refreshed on an explicit check.
- ``update_available`` (Boolean, default 0) — cached comparison result.
- ``latest_checked_at`` (DateTime, nullable) — when the latest lookup ran.
- ``latest_error`` (String(512), nullable) — check failure detail.
- ``update_status`` (String(16), nullable) — "updating" | "success" | "failed".
- ``update_error`` (String(512), nullable) — update failure detail.
- ``update_started_at`` (DateTime, nullable) — when the update was triggered.

Rollback drops the table. New table, so no backfill.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "056"
down_revision: str = "055"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "machine_engine_status",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "machine_id",
            sa.String(36),
            sa.ForeignKey("machines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine", sa.String(128), nullable=False),
        sa.Column("latest_version", sa.String(64), nullable=True),
        sa.Column(
            "update_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("latest_checked_at", sa.DateTime(), nullable=True),
        sa.Column("latest_error", sa.String(512), nullable=True),
        sa.Column("update_status", sa.String(16), nullable=True),
        sa.Column("update_error", sa.String(512), nullable=True),
        sa.Column("update_started_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("machine_id", "engine", name="uq_machine_engine_status"),
    )
    op.create_index(
        "ix_machine_engine_status_machine", "machine_engine_status", ["machine_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_machine_engine_status_machine", table_name="machine_engine_status"
    )
    op.drop_table("machine_engine_status")
