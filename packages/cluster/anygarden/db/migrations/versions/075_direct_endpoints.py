"""Add local direct endpoint policy and encrypted agent credentials."""

import sqlalchemy as sa
from alembic import op

revision = "075_direct_endpoints"
down_revision = "074_usage_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("base_url", sa.String(2048), nullable=True))
    op.add_column("agents", sa.Column("api_protocol", sa.String(32), nullable=True))
    op.add_column("agents", sa.Column("credential_ref", sa.String(36), nullable=True))
    op.create_table(
        "engine_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine", sa.String(128), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_engine_credentials_agent_id", "engine_credentials", ["agent_id"]
    )


def downgrade():
    op.drop_table("engine_credentials")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("credential_ref")
        batch.drop_column("api_protocol")
        batch.drop_column("base_url")
