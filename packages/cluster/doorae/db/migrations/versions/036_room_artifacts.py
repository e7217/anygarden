"""Add room_artifacts table.

Revision ID: 036
Revises: 035
Create Date: 2026-04-28

Rationale
---------
Issue #290 (Phase B) — separate the agent-produced artifact channel
from the user-uploaded ``room_shared_files`` flow. The two streams
have inverted directions (agent → user vs. user → agent) and
different policies (binary MIME whitelist + larger size budget vs.
text-only + 256 KB) that justify a distinct table even though both
keep metadata + sha256 here and bytes on disk under
``settings.artifact_files_dir/<room_id>/<id>``.

``produced_by_agent_id`` is ``ON DELETE SET NULL`` so removing an
agent does not cascade into its prior artifacts — the artifact
belongs to the room. ``room_id`` is ``CASCADE`` so dropping the room
collects the rows in one step.

A ``UniqueConstraint(room_id, sha256)`` makes re-delivery idempotent:
the machine daemon polls the agent's outbox and re-emits unchanged
files on reconnect; this constraint lets the server treat duplicates
as no-ops without bookkeeping the seen set in memory.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: str = "035"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "room_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("room_id", sa.String(length=36), nullable=False),
        sa.Column("produced_by_agent_id", sa.String(length=36), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["produced_by_agent_id"], ["agents.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "room_id", "sha256", name="uq_room_artifact_sha"
        ),
    )
    # The "list artifacts in this room" query runs on every panel
    # open, so a dedicated index on ``room_id`` earns its keep.
    op.create_index(
        "ix_room_artifacts_room_id",
        "room_artifacts",
        ["room_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_room_artifacts_room_id", table_name="room_artifacts"
    )
    op.drop_table("room_artifacts")
