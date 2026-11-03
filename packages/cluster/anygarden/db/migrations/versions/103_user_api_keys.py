"""Personal API keys for calling the API from outside the browser (#988).

``user_api_keys`` stores the argon2 hash and lookup hint of each key, its
scope (read / write / admin), expiry, revocation and last use.

Revision ID: 103_user_api_keys
Revises: 102_agent_room_session_reset
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "103_user_api_keys"
down_revision = "102_agent_room_session_reset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(512), nullable=False),
        sa.Column("lookup_hint", sa.String(12), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=True),
        sa.Column("expires_at", UtcDateTime(), nullable=True),
        sa.Column("revoked_at", UtcDateTime(), nullable=True),
        sa.Column("last_used_at", UtcDateTime(), nullable=True),
    )
    op.create_index("ix_user_api_keys_hint", "user_api_keys", ["lookup_hint"])
    op.create_index("ix_user_api_keys_user", "user_api_keys", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_api_keys_user", table_name="user_api_keys")
    op.drop_index("ix_user_api_keys_hint", table_name="user_api_keys")
    op.drop_table("user_api_keys")
