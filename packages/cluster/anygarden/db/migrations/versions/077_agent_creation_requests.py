"""Persist idempotency for agent creation retries."""

import sqlalchemy as sa
from alembic import op

revision = "077_agent_creation_requests"
down_revision = "076_pi_native_auth"
branch_labels = None
depends_on = None


def upgrade():
    # ADD COLUMN + CREATE INDEX avoids rebuilding the heavily referenced
    # agents table on SQLite. Existing agents and related rows stay intact.
    op.add_column("agents", sa.Column("creation_request_key", sa.String(64), nullable=True))
    op.add_column("agents", sa.Column("creation_request_fingerprint", sa.String(64), nullable=True))
    op.create_index("uq_agents_creation_request_key", "agents", ["creation_request_key"], unique=True)


def downgrade():
    op.drop_index("uq_agents_creation_request_key", table_name="agents")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("creation_request_fingerprint")
        batch.drop_column("creation_request_key")
