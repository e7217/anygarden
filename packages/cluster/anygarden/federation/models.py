"""Durable peer trust records, deliberately separate from local identities."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from anygarden.db.models import Base
from anygarden.db.types import UtcDateTime


class Peer(Base):
    __tablename__ = "federation_peers"
    node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    certificate_pem: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    endpoint: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16))
    epoch: Mapped[int] = mapped_column(Integer)
    approved_by: Mapped[str] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class PeerInvite(Base):
    __tablename__ = "federation_invites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    intended_node_id: Mapped[str] = mapped_column(String(36), index=True)
    certificate_pem: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    endpoint: Mapped[dict] = mapped_column(JSON)
    token_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    # Persist the epoch seen on creation: stale invitations cannot undo revoke.
    expected_peer_epoch: Mapped[int] = mapped_column(Integer)
    scopes: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    grant_expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    approved_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PeerGrant(Base):
    __tablename__ = "federation_grants"
    peer_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean)
    actors: Mapped[list] = mapped_column(JSON)
    capabilities: Mapped[list] = mapped_column(JSON)
    role: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    approved_by: Mapped[str] = mapped_column(String(36))


class PeerControlEvent(Base):
    __tablename__ = "federation_control_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    peer_node_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    peer_epoch: Mapped[int] = mapped_column(Integer)
    channel_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    grant_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)


class PeerAudit(Base):
    __tablename__ = "federation_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    peer_node_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class PeerAcceptance(Base):
    __tablename__ = "federation_acceptances"
    invite_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issuer_node_id: Mapped[str] = mapped_column(String(36))
    bundle_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    expected_peer_epoch: Mapped[int] = mapped_column(Integer)
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(36))


class PeerConsent(Base):
    __tablename__ = "federation_consents"
    peer_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Distinct local-policy epoch; never serialized as authority grant_epoch.
    policy_epoch: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean)
