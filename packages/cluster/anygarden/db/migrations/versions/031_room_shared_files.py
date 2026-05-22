"""Add room_shared_files table.

Revision ID: 031
Revises: 030
Create Date: 2026-04-23

Rationale
---------
Issue #246 — a room can carry attached files that are
copy-distributed to every participating agent's
``memory/shared/`` directory. The DB only keeps metadata + sha256 for
each attachment; the raw bytes sit on disk under
``settings.room_files_dir/<room_id>/<id>`` so the default SQLite
``anygarden.db`` stays compact even when rooms accumulate many
attachments.

``uploaded_by`` uses ``ON DELETE SET NULL`` so deleting the uploader
does not cascade to the file row — the attachment belongs to the
room, not the person who happened to upload it. ``room_id`` is the
opposite: ``CASCADE`` so that dropping the room cleans up its
attachments in one step.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "031"
down_revision: str = "030"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "room_shared_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("room_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("uploaded_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "room_id", "storage_name", name="uq_room_shared_storage"
        ),
    )
    # The "list files in this room" query runs on every room open,
    # so a dedicated index on ``room_id`` earns its keep.
    op.create_index(
        "ix_room_shared_files_room_id",
        "room_shared_files",
        ["room_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_room_shared_files_room_id", table_name="room_shared_files"
    )
    op.drop_table("room_shared_files")
