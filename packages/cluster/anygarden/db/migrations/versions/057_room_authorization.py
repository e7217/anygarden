"""Add private visibility and archive state for room authorization.

Revision ID: 057
Revises: 056
Create Date: 2026-08-05

The Phase 1 authorization contract needs room state to be explicit rather
than inferred from route behavior.  This migration:

* backfills every existing room to the MVP-only ``private`` visibility;
* adds reversible archive provenance (``archived_at`` / ``archived_by``);
* normalizes legacy participant roles into the supported vocabulary; and
* adds indexes used by per-request/per-frame authorization resolution.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "057"
down_revision: str = "056"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # SQLite cannot add a foreign-key constraint with plain ALTER TABLE;
    # batch mode rebuilds the table while preserving its existing indexes and
    # constraints. The explicit FK name is required by Alembic batch mode.
    with op.batch_alter_table("rooms") as batch:
        batch.add_column(
            sa.Column(
                "visibility",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'private'"),
            )
        )
        batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column(
                "archived_by",
                sa.String(36),
                sa.ForeignKey(
                    "users.id",
                    ondelete="SET NULL",
                    name="fk_rooms_archived_by",
                ),
                nullable=True,
            )
        )
    op.create_index(
        "ix_rooms_visibility_archived",
        "rooms",
        ["visibility", "archived_at"],
    )

    # Old API inputs accepted arbitrary strings. Normalize unknown values so
    # the new fail-closed policy starts from a deterministic role vocabulary.
    op.execute(
        "UPDATE participants SET role = 'member' "
        "WHERE role NOT IN ('observer', 'member', 'admin', 'owner')"
    )
    op.create_index(
        "ix_participants_room_role",
        "participants",
        ["room_id", "role"],
    )


def downgrade() -> None:
    op.drop_index("ix_participants_room_role", table_name="participants")
    op.drop_index("ix_rooms_visibility_archived", table_name="rooms")
    with op.batch_alter_table("rooms") as batch:
        batch.drop_column("archived_by")
        batch.drop_column("archived_at")
        batch.drop_column("visibility")
