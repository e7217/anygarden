"""Dedupe duplicate participants and add per-room uniqueness.

Revision ID: 052
Revises: 051
Create Date: 2026-07-11

Rationale
---------
Issue #519 — the ``participants`` table had no uniqueness guard, so
non-idempotent add paths (and races) could leave a user or agent with
two+ rows in the same room. ``require_room_member`` fetched a single row
and raised ``MultipleResultsFound`` on the duplicate, which 500'd the
messages/read REST endpoints and was swallowed as a false 4003 on the WS
handshake — bricking the whole room for that member.

This migration:

1. **Dedupes** existing rows: for each ``(room_id, user_id)`` and
   ``(room_id, agent_id)`` group it keeps one row — admin/owner first
   (so the caller's highest privilege survives), then earliest
   ``joined_at``, then ``id`` as a deterministic tie-break — and deletes
   the rest. Dropped duplicates are spurious copies; their per-row
   sidebar state (``pinned`` / ``last_read_message_seq``) is discarded,
   which at worst re-marks a couple of messages unread on the kept row.
2. Adds partial UNIQUE indexes mirroring ``Participant.__table_args__``.
   Partial (``… IS NOT NULL``) because ``user_id`` / ``agent_id`` are
   mutually-exclusive nullable columns — a plain composite UNIQUE would
   let SQLite's NULL-distinct rule wave every duplicate through.

Rollback drops the indexes; the deleted duplicate rows are not restored
(they were redundant by definition).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "052"
down_revision: str = "051"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


# Keep row rn=1 per group; delete the rest. ROW_NUMBER() is supported by
# SQLite (3.25+) and PostgreSQL alike. ``{col}`` is user_id / agent_id.
_DEDUPE = """
    DELETE FROM participants
    WHERE id IN (
        SELECT id FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY room_id, {col}
                       ORDER BY (CASE WHEN role IN ('admin', 'owner') THEN 0 ELSE 1 END),
                                joined_at ASC, id ASC
                   ) AS rn
            FROM participants
            WHERE {col} IS NOT NULL
        )
        WHERE rn > 1
    )
"""


def upgrade() -> None:
    op.execute(_DEDUPE.format(col="user_id"))
    op.execute(_DEDUPE.format(col="agent_id"))

    op.create_index(
        "uq_participants_room_user",
        "participants",
        ["room_id", "user_id"],
        unique=True,
        sqlite_where=sa.text("user_id IS NOT NULL"),
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_participants_room_agent",
        "participants",
        ["room_id", "agent_id"],
        unique=True,
        sqlite_where=sa.text("agent_id IS NOT NULL"),
        postgresql_where=sa.text("agent_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_participants_room_agent", table_name="participants")
    op.drop_index("uq_participants_room_user", table_name="participants")
