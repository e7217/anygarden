"""Durable shared-channel streams and projections (#591)."""

import sqlalchemy as sa
from alembic import op

revision = "065_shared_channels"
down_revision = "064_peer_trust"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shared_channel_streams",
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "local_room_id", sa.String(length=36), primary_key=False, nullable=False
        ),
        sa.Column("last_seq", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("applied_seq", sa.BigInteger(), primary_key=False, nullable=False),
        sa.ForeignKeyConstraint(["local_room_id"], ["rooms.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(*["local_room_id"], name=None),
    )
    op.create_table(
        "shared_channel_deliveries",
        sa.Column(
            "peer_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("ack_seq", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("delivered_seq", sa.BigInteger(), primary_key=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "shared_channel_events",
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("seq", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), primary_key=False, nullable=False),
        sa.Column("body", sa.Text(), primary_key=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            *["authority_node_id", "channel_id", "event_id"],
            name="uq_shared_event_identity",
        ),
    )
    op.create_table(
        "shared_channel_submissions",
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("request_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("body", sa.Text(), primary_key=False, nullable=False),
        sa.Column("state", sa.String(length=24), primary_key=False, nullable=False),
        sa.Column("receipt", sa.JSON(), primary_key=False, nullable=True),
        sa.Column("error_code", sa.String(length=48), primary_key=False, nullable=True),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "shared_command_receipts",
        sa.Column(
            "sender_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("request_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("body", sa.Text(), primary_key=False, nullable=False),
        sa.Column("receipt", sa.JSON(), primary_key=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "shared_inbox_events",
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("seq", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), primary_key=False, nullable=False),
        sa.Column("body", sa.Text(), primary_key=False, nullable=False),
        sa.Column("applied", sa.Boolean(), primary_key=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            *["authority_node_id", "channel_id", "event_id"],
            name="uq_shared_inbox_identity",
        ),
    )
    op.create_table(
        "shared_message_origins",
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("message_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "local_message_id", sa.String(length=36), primary_key=False, nullable=False
        ),
        sa.Column(
            "thread_root_id", sa.String(length=36), primary_key=False, nullable=True
        ),
        sa.Column("actor", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("seq", sa.BigInteger(), primary_key=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["local_message_id"], ["messages.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(*["local_message_id"], name=None),
    )
    op.create_table(
        "shared_participant_operations",
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "operation_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("body", sa.Text(), primary_key=False, nullable=False),
        sa.Column("event", sa.JSON(), primary_key=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "shared_participants",
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("node_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=8), primary_key=True, nullable=False),
        sa.Column(
            "principal_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("role", sa.String(length=16), primary_key=False, nullable=False),
        sa.Column("active", sa.Boolean(), primary_key=False, nullable=False),
        sa.Column("revision", sa.BigInteger(), primary_key=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "shared_publication_consents",
        sa.Column(
            "authority_node_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column("channel_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("node_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=8), primary_key=True, nullable=False),
        sa.Column(
            "principal_id", sa.String(length=36), primary_key=True, nullable=False
        ),
        sa.Column(
            "approved_by", sa.String(length=36), primary_key=False, nullable=False
        ),
        sa.Column("active", sa.Boolean(), primary_key=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="RESTRICT"),
    )


def downgrade():
    op.drop_table("shared_publication_consents")
    op.drop_table("shared_participants")
    op.drop_table("shared_participant_operations")
    op.drop_table("shared_message_origins")
    op.drop_table("shared_inbox_events")
    op.drop_table("shared_command_receipts")
    op.drop_table("shared_channel_submissions")
    op.drop_table("shared_channel_events")
    op.drop_table("shared_channel_deliveries")
    op.drop_table("shared_channel_streams")
