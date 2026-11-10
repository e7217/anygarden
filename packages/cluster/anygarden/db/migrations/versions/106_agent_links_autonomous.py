"""Use agent descriptions instead of per-link conditions (#1009).

Links and their audit fields are preserved. The discarded condition text
cannot be recovered by downgrade; restore a pre-upgrade backup if needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "106_agent_links_autonomous"
down_revision = "105_agent_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_links") as batch:
        batch.drop_column("when_to_ask")


def downgrade() -> None:
    with op.batch_alter_table("agent_links") as batch:
        batch.add_column(sa.Column("when_to_ask", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("agent_links") as batch:
        batch.alter_column("when_to_ask", server_default=None)
