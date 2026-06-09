"""Promote activity_logs.room_id to a first-class indexed column.

Revision ID: 041
Revises: 040
Create Date: 2026-06-09

Issue #427 — per-room activity timelines (the new /rooms/{id}/activity
endpoint) filter by room. ``room_id`` previously lived only inside the
``details`` JSON, so that query meant a full scan + json_extract. This
adds a nullable indexed column and backfills it from existing rows'
``details``. Backfill is a portable Python pass (SQLite stores JSON as
TEXT, Postgres as jsonb) — fine at current scale; orphan/turn rows are
modest in volume.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "041"
down_revision: str = "040"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "activity_logs",
        sa.Column("room_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_activity_logs_room_ts",
        "activity_logs",
        ["room_id", "timestamp"],
    )

    # Backfill from details->room_id.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, details FROM activity_logs "
            "WHERE room_id IS NULL AND details IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        raw = row[1]
        if isinstance(raw, dict):
            details = raw
        elif isinstance(raw, (str, bytes)):
            try:
                details = json.loads(raw)
            except (ValueError, TypeError):
                continue
        else:
            continue
        room_id = details.get("room_id") if isinstance(details, dict) else None
        if room_id:
            conn.execute(
                sa.text("UPDATE activity_logs SET room_id = :r WHERE id = :i"),
                {"r": room_id, "i": row[0]},
            )


def downgrade() -> None:
    op.drop_index("ix_activity_logs_room_ts", table_name="activity_logs")
    op.drop_column("activity_logs", "room_id")
