"""Create ``room_invite_links`` table (§11 design doc).

Revision ID: 014
Revises: 013
Create Date: 2026-04-15

Rationale
---------
PR B of the anonymous-guest RFC (#22). Stores the issue/list/revoke
state for shareable invite tokens; validation and guest-session
issuance arrive in PR C. Schema mirrors ``AgentToken`` — only the
argon2 hash and a 12-char ``lookup_hint`` land in the DB, never the
plaintext.

Dropping a room cascades into its invites (no dangling links).
Deleting the issuing admin also cascades — aligned with how other
tables handle admin removal.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "014"
down_revision: str = "013"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "room_invite_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "room_id",
            sa.String(36),
            sa.ForeignKey("rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(512), nullable=False),
        sa.Column("lookup_hint", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column(
            "use_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_index(
        "ix_room_invite_links_room",
        "room_invite_links",
        ["room_id"],
    )
    op.create_index(
        "ix_room_invite_links_hint",
        "room_invite_links",
        ["lookup_hint"],
    )


def downgrade() -> None:
    op.drop_index("ix_room_invite_links_hint", table_name="room_invite_links")
    op.drop_index("ix_room_invite_links_room", table_name="room_invite_links")
    op.drop_table("room_invite_links")
