"""Keep agents on their machine: drop the ``restart_anywhere`` default (#907).

A placed agent owns machine-local state (workspace, memory, the machine's
Codex login, host access for trusted agents). ``restart_anywhere`` let a
crash loop move it to any machine without the admin knowing. Existing rows
switch to ``restart_on_same_machine``; the server no longer re-places an
agent on its own either way.

Downgrade restores only the column default. Rows are not switched back:
the server treats both values the same after #907, so the data change is
harmless to keep and there is no record of which rows were changed.

Revision ID: 094_sticky_agent_placement
Revises: 093_turn_attempt_transient_retry
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "094_sticky_agent_placement"
down_revision = "093_turn_attempt_transient_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE agents SET restart_policy = 'restart_on_same_machine' "
        "WHERE restart_policy = 'restart_anywhere'"
    )
    with op.batch_alter_table("agents") as batch:
        batch.alter_column(
            "restart_policy",
            existing_type=sa.String(64),
            server_default="restart_on_same_machine",
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.alter_column(
            "restart_policy",
            existing_type=sa.String(64),
            server_default="restart_anywhere",
        )
