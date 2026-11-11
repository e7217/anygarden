"""Record when an archived room's artifact bytes were removed (#1018).

The row stays as a tombstone so links to the file report that it expired.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "107_room_artifact_purged_at"
down_revision = "106_agent_links_autonomous"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("room_artifacts") as batch:
        batch.add_column(sa.Column("purged_at", UtcDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("room_artifacts") as batch:
        batch.drop_column("purged_at")
