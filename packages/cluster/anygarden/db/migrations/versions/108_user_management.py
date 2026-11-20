"""Account suspension, login tracking and node settings (#1058).

``users`` gains ``disabled_at`` (suspended accounts), ``last_login_at``
and ``credentials_changed_at`` (sessions issued before it are rejected).
``server_settings`` holds node-wide settings an admin changes at
runtime, starting with whether new accounts may register.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "108_user_management"
down_revision = "107_room_artifact_purged_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("disabled_at", UtcDateTime(), nullable=True))
        batch.add_column(sa.Column("last_login_at", UtcDateTime(), nullable=True))
        batch.add_column(
            sa.Column("credentials_changed_at", UtcDateTime(), nullable=True)
        )
    op.create_table(
        "server_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("server_settings")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("credentials_changed_at")
        batch.drop_column("last_login_at")
        batch.drop_column("disabled_at")
