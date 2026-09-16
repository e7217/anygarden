"""Peer trust, one-use invites, scoped grants and revocation outbox (#590)."""

import sqlalchemy as sa
from alembic import op
from anygarden.db.types import UtcDateTime

revision = "064_peer_trust"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "federation_acceptances",
        sa.Column("invite_id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "issuer_node_id", sa.String(length=36), nullable=False, primary_key=False
        ),
        sa.Column(
            "bundle_hash", sa.String(length=64), nullable=False, primary_key=False
        ),
        sa.Column("state", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column(
            "expected_peer_epoch", sa.Integer(), nullable=False, primary_key=False
        ),
        sa.Column("receipt", sa.JSON(), nullable=True, primary_key=False),
        sa.Column(
            "approved_by", sa.String(length=36), nullable=False, primary_key=False
        ),
    )
    op.create_table(
        "federation_audit",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("actor_id", sa.String(length=36), nullable=False, primary_key=False),
        sa.Column(
            "peer_node_id", sa.String(length=36), nullable=False, primary_key=False
        ),
        sa.Column("action", sa.String(length=32), nullable=False, primary_key=False),
        sa.Column("created_at", UtcDateTime(), nullable=False, primary_key=False),
    )
    op.create_table(
        "federation_consents",
        sa.Column(
            "peer_node_id", sa.String(length=36), nullable=False, primary_key=True
        ),
        sa.Column(
            "authority_node_id", sa.String(length=36), nullable=False, primary_key=True
        ),
        sa.Column("channel_id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("policy_epoch", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("active", sa.Boolean(), nullable=False, primary_key=False),
    )
    op.create_table(
        "federation_control_events",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "peer_node_id", sa.String(length=36), nullable=False, primary_key=False
        ),
        sa.Column("kind", sa.String(length=32), nullable=False, primary_key=False),
        sa.Column("peer_epoch", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("channel_id", sa.String(36), nullable=True),
        sa.Column("grant_epoch", sa.Integer(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False, primary_key=False),
        sa.Column("delivered", sa.Boolean(), nullable=False, primary_key=False),
    )
    op.create_index(
        "ix_federation_control_events_peer_node_id",
        "federation_control_events",
        ["peer_node_id"],
    )
    op.create_table(
        "federation_grants",
        sa.Column(
            "peer_node_id", sa.String(length=36), nullable=False, primary_key=True
        ),
        sa.Column(
            "authority_node_id", sa.String(length=36), nullable=False, primary_key=True
        ),
        sa.Column("channel_id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("epoch", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("active", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("actors", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("capabilities", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("role", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False, primary_key=False),
        sa.Column(
            "approved_by", sa.String(length=36), nullable=False, primary_key=False
        ),
    )
    op.create_table(
        "federation_invites",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "intended_node_id", sa.String(length=36), nullable=False, primary_key=False
        ),
        sa.Column("certificate_pem", sa.Text(), nullable=False, primary_key=False),
        sa.Column(
            "fingerprint", sa.String(length=64), nullable=False, primary_key=False
        ),
        sa.Column("endpoint", sa.JSON(), nullable=False, primary_key=False),
        sa.Column(
            "token_hash", sa.String(length=64), nullable=False, primary_key=False
        ),
        sa.Column("state", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column(
            "expected_peer_epoch", sa.Integer(), nullable=False, primary_key=False
        ),
        sa.Column("scopes", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False, primary_key=False),
        sa.Column("grant_expires_at", UtcDateTime(), nullable=False, primary_key=False),
        sa.Column(
            "approved_by", sa.String(length=36), nullable=False, primary_key=False
        ),
        sa.Column("created_at", UtcDateTime(), nullable=False, primary_key=False),
        sa.Column("receipt", sa.JSON(), nullable=True, primary_key=False),
    )
    op.create_index(
        "ix_federation_invites_intended_node_id",
        "federation_invites",
        ["intended_node_id"],
    )
    op.create_table(
        "federation_peers",
        sa.Column("node_id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("certificate_pem", sa.Text(), nullable=False, primary_key=False),
        sa.Column(
            "fingerprint", sa.String(length=64), nullable=False, primary_key=False
        ),
        sa.Column("endpoint", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("state", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("epoch", sa.Integer(), nullable=False, primary_key=False),
        sa.Column(
            "approved_by", sa.String(length=36), nullable=False, primary_key=False
        ),
        sa.Column("updated_at", UtcDateTime(), nullable=False, primary_key=False),
    )


def downgrade():
    op.drop_table("federation_peers")
    op.drop_table("federation_invites")
    op.drop_table("federation_grants")
    op.drop_table("federation_control_events")
    op.drop_table("federation_consents")
    op.drop_table("federation_audit")
    op.drop_table("federation_acceptances")
