"""Channel-lifetime receipts, ordered log, mirror inbox and unconfirmed submissions."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from anygarden.db.models import Base


class ChannelStream(Base):
    __tablename__ = "shared_channel_streams"
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    local_room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="RESTRICT"), unique=True
    )
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    applied_seq: Mapped[int] = mapped_column(BigInteger, default=0)


class CommandReceipt(Base):
    __tablename__ = "shared_command_receipts"
    sender_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    receipt: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )


class ChannelEvent(Base):
    __tablename__ = "shared_channel_events"
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint(
            "authority_node_id",
            "channel_id",
            "event_id",
            name="uq_shared_event_identity",
        ),
        ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )


class InboxEvent(Base):
    __tablename__ = "shared_inbox_events"
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint(
            "authority_node_id",
            "channel_id",
            "event_id",
            name="uq_shared_inbox_identity",
        ),
        ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )


class ChannelDelivery(Base):
    __tablename__ = "shared_channel_deliveries"
    peer_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ack_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    delivered_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )


class SharedMessage(Base):
    __tablename__ = "shared_message_origins"
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    local_message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="RESTRICT"), unique=True
    )
    thread_root_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor: Mapped[dict] = mapped_column(JSON)
    seq: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )


class ChannelSubmission(Base):
    __tablename__ = "shared_channel_submissions"
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), default="unconfirmed")
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )


class SharedParticipant(Base):
    """Display projection only: deliberately has no local credentials or membership."""

    __tablename__ = "shared_participants"
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean)
    revision: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )


class PublicationConsent(Base):
    __tablename__ = "shared_publication_consents"
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    approved_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    active: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (
        ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )


class ParticipantOperation(Base):
    __tablename__ = "shared_participant_operations"
    authority_node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    event: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        ForeignKeyConstraint(
            ["authority_node_id", "channel_id"],
            [
                "shared_channel_streams.authority_node_id",
                "shared_channel_streams.channel_id",
            ],
            ondelete="RESTRICT",
        ),
    )
