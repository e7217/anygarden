"""Add explicit agent provider without guessing defaults for existing agents."""
from alembic import op
import sqlalchemy as sa

revision = "073_agent_provider"
down_revision = "072_drop_agent_collaboration_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("provider", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("provider")
