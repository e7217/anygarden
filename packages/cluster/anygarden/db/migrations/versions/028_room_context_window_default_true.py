"""Flip rooms.context_window_enabled server default to True.

Revision ID: 028
Revises: 027
Create Date: 2026-04-21

Rationale
---------
Issue #225 — the natural UX for a multi-agent chat product is
"other agents' responses are shared as ambient context by
default". Issue #148 landed the column with ``server_default='0'``
to preserve pre-#148 semantics; with Part 3 of #148 now wired and
the per-agent ``context_window_opt_out`` escape hatch in place
(#148 Part 2 / migration 023), the room-level default should flip
to True so fresh rooms opt into ambient sharing without operator
intervention.

Only the DDL default is changed. Existing ``context_window_enabled
= 0`` rows are **preserved** — they represent either rooms created
under the old default or rooms an admin explicitly turned off, and
the migration has no way to tell the two apart. Admins can still
toggle individual rooms on via the (now admin-only) room edit
dialog.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "028"
down_revision: str = "027"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ``batch_alter_table`` for SQLite compatibility — matches the
    # style of 022_room_context_window.py which originally added the
    # column. No row-level UPDATE: existing values stay as-is.
    with op.batch_alter_table("rooms") as batch:
        batch.alter_column(
            "context_window_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.text("1"),
        )


def downgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.alter_column(
            "context_window_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.text("0"),
        )
