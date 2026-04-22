"""Add rooms.ephemeral column.

Revision ID: 029
Revises: 028
Create Date: 2026-04-22

Rationale
---------
Issue #237 — the two-axis approach to DM memory management needs an
explicit "this session is temporary" signal that propagates through
the WS welcome frame into each engine adapter's ``system_prompt``.
The ephemeral flag is a trust-model hint (see plan §3.2 decision 3),
not a hard filesystem guard — we tell the agent "don't append to
memory/notes.md in this room" and rely on system-prompt compliance.

Default ``FALSE`` preserves existing DM behaviour. Admins/DM owners
toggle individual rooms via the room header (PATCH ``ephemeral``).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "029"
down_revision: str = "028"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # SQLite batch mode for parity with 022/023. ``server_default`` is
    # load-bearing: adding NOT NULL columns on a populated table fails
    # without a backfill default.
    with op.batch_alter_table("rooms") as batch:
        batch.add_column(
            sa.Column(
                "ephemeral",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.drop_column("ephemeral")
