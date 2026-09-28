"""Record how a machine's engine is signed in (#715)."""

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "078_machine_engine_auth_status"
down_revision = "077_agent_creation_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "machine_engine_status", sa.Column("auth_status", sa.String(16), nullable=True)
    )
    op.add_column(
        "machine_engine_status", sa.Column("auth_checked_at", UtcDateTime(), nullable=True)
    )


def downgrade():
    with op.batch_alter_table("machine_engine_status") as batch:
        batch.drop_column("auth_checked_at")
        batch.drop_column("auth_status")
