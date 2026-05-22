"""Add participants.last_read_message_seq.

Revision ID: 040
Revises: 039
Create Date: 2026-05-20

Issue #385 — sidebar rows need a cheap per-user "has unread updates"
bit. The read cursor belongs to room membership, so storing the latest
seen room-local ``messages.seq`` on ``participants`` keeps the state
co-located with the user/room edge and cascades naturally when
membership is removed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "040"
down_revision: str = "039"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "participants",
        sa.Column("last_read_message_seq", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("participants", "last_read_message_seq")
