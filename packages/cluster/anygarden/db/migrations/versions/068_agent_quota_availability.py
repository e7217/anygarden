"""Agent quota availability as first-class state (D-2, issue #625).

Adds ``agents.unavailable_until`` — the bounded reset instant paired with a
``quota_exhausted`` unavailability code. NULL means "no known bound": the
Raft-style pattern keeps the agent visibly blocked while a fresh successful
engine call (or an explicit reset) clears it.
"""

import sqlalchemy as sa
from alembic import op

revision = "068_agent_quota_availability"
down_revision = "067_delegation_pickup_timeout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column("unavailable_until", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("unavailable_until")
