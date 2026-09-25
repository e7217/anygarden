"""Add agent-scoped native Pi credentials."""

import sqlalchemy as sa
from alembic import op

revision = "076_pi_native_auth"
down_revision = "075_direct_endpoints"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pi_native_credentials",
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("pi_native_credentials")
