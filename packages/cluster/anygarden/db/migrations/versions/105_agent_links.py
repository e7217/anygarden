"""Agent links and question chains (#1006).

``agent_links`` lets one agent ask another from any room. ``ask_peer``
seats a linked agent that is not in the room for the question's thread
only (``participants.joined_via = 'link'``). Peers may now ask on, so a
group records the question it answers and the chain it belongs to, and a
question waits for its peer's own questions (``awaiting_group_id``).

Revision ID: 105_agent_links
Revises: 104_user_project_order
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from anygarden.db.types import UtcDateTime

revision = "105_agent_links"
down_revision = "104_user_project_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "from_agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("when_to_ask", sa.Text(), nullable=False),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", UtcDateTime(), nullable=True),
        sa.Column("updated_at", UtcDateTime(), nullable=True),
        sa.UniqueConstraint("from_agent_id", "to_agent_id", name="uq_agent_links_pair"),
        sa.CheckConstraint("from_agent_id <> to_agent_id", name="ck_agent_links_not_self"),
    )
    op.create_index("ix_agent_links_from", "agent_links", ["from_agent_id"])

    with op.batch_alter_table("participants") as batch:
        batch.add_column(sa.Column("joined_via", sa.String(16), nullable=True))

    with op.batch_alter_table("peer_ask_groups") as batch:
        batch.add_column(sa.Column("parent_target_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("chain_id", sa.String(36), nullable=True))
        batch.add_column(
            sa.Column("depth", sa.Integer(), nullable=False, server_default="1")
        )
    op.create_index("ix_peer_ask_groups_chain_id", "peer_ask_groups", ["chain_id"])
    # Existing groups are one-level chains of their own.
    op.execute("UPDATE peer_ask_groups SET chain_id = id WHERE chain_id IS NULL")

    with op.batch_alter_table("peer_ask_targets") as batch:
        batch.add_column(sa.Column("awaiting_group_id", sa.String(36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("peer_ask_targets") as batch:
        batch.drop_column("awaiting_group_id")
    op.drop_index("ix_peer_ask_groups_chain_id", table_name="peer_ask_groups")
    with op.batch_alter_table("peer_ask_groups") as batch:
        batch.drop_column("depth")
        batch.drop_column("chain_id")
        batch.drop_column("parent_target_id")
    with op.batch_alter_table("participants") as batch:
        batch.drop_column("joined_via")
    op.drop_index("ix_agent_links_from", table_name="agent_links")
    op.drop_table("agent_links")
