"""SQLAlchemy ORM models for the Doorae chat server."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Float,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base for all Doorae models."""
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    rooms: Mapped[list["Room"]] = relationship("Room", back_populates="project")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    parent_room_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True, default=None
    )
    is_dm: Mapped[bool] = mapped_column(Boolean, default=False)
    representative_agent_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped["Project"] = relationship("Project", back_populates="rooms")
    parent_room: Mapped[Optional["Room"]] = relationship(
        "Room", remote_side="Room.id", back_populates="child_rooms"
    )
    child_rooms: Mapped[list["Room"]] = relationship("Room", back_populates="parent_room")
    participants: Mapped[list["Participant"]] = relationship("Participant", back_populates="room")
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="room")
    representative_agent: Mapped[Optional["Agent"]] = relationship(
        "Agent", foreign_keys=[representative_agent_id]
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_placed_state", "placed_on_machine_id", "actual_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    engine: Mapped[str] = mapped_column(String(128), nullable=False)
    placed_on_machine_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("machines.id", ondelete="SET NULL"), nullable=True, default=None
    )
    desired_state: Mapped[str] = mapped_column(String(32), default="idle")
    actual_state: Mapped[str] = mapped_column(String(32), default="idle")
    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    profile_yaml: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    # Per-agent directory manifest: AGENTS.md source of truth. The machine
    # materializes this into ~/.doorae/agents/<id>/AGENTS.md on spawn.
    # See docs/plans/2026-04-11-per-agent-directory-skills.md
    agents_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_crash_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    reasoning_effort: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default=None
    )
    restart_policy: Mapped[str] = mapped_column(String(64), default="restart_anywhere")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    max_restarts: Mapped[int] = mapped_column(Integer, default=3)
    restart_window_seconds: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    machine: Mapped[Optional["Machine"]] = relationship("Machine", back_populates="agents")
    files: Mapped[list["AgentFile"]] = relationship(
        "AgentFile", back_populates="agent", cascade="all, delete-orphan"
    )


class Machine(Base):
    __tablename__ = "machines"
    __table_args__ = (
        Index("ix_machines_status_owner", "status", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="offline")
    daemon_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)
    daemon_last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    cpu_cores: Mapped[int] = mapped_column(Integer, default=0)
    memory_gb: Mapped[float] = mapped_column(Float, default=0.0)
    # Placement capacity limit. Hidden from UI/API/CLI as of 2026-04-15
    # (issue #2) — kept in the schema so ``placement.py`` can still
    # enforce a soft cap and we can re-expose it later without a
    # migration. Set absurdly high so it never bites in practice.
    max_agents: Mapped[int] = mapped_column(Integer, default=1000)
    labels: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    owner: Mapped["User"] = relationship("User")
    agents: Mapped[list["Agent"]] = relationship("Agent", back_populates="machine")
    # cascade + passive_deletes: let the DB's ON DELETE CASCADE handle
    # these. Without passive_deletes the ORM tries to UPDATE machine_id
    # to NULL on delete, which fails NOT NULL on these tables.
    engines: Mapped[list["MachineEngine"]] = relationship(
        "MachineEngine",
        back_populates="machine",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tokens: Mapped[list["MachineToken"]] = relationship(
        "MachineToken",
        back_populates="machine",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class MachineEngine(Base):
    """Records which engines a machine supports and their versions."""
    __tablename__ = "machine_engines"
    __table_args__ = (
        Index("ix_machine_engines_engine", "engine"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    machine_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    machine: Mapped["Machine"] = relationship("Machine", back_populates="engines")


class MachineToken(Base):
    """Stores hashed machine tokens for daemon authentication."""
    __tablename__ = "machine_tokens"
    __table_args__ = (
        Index("ix_machine_tokens_hint", "lookup_hint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    machine_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    lookup_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    machine: Mapped["Machine"] = relationship("Machine", back_populates="tokens")


class AgentToken(Base):
    """Stores hashed agent tokens for agent authentication (O(1) lookup)."""
    __tablename__ = "agent_tokens"
    __table_args__ = (
        Index("ix_agent_tokens_hint", "lookup_hint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    lookup_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    agent: Mapped["Agent"] = relationship("Agent")


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, default=None
    )
    agent_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, default=None
    )
    role: Mapped[str] = mapped_column(String(32), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    room: Mapped["Room"] = relationship("Room", back_populates="participants")
    user: Mapped[Optional["User"]] = relationship("User")
    agent: Mapped[Optional["Agent"]] = relationship("Agent")
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="participant",
        passive_deletes=True,
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("room_id", "seq", name="uq_room_seq"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    participant_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("participants.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    extra_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=None)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    room: Mapped["Room"] = relationship("Room", back_populates="messages")
    participant: Mapped[Optional["Participant"]] = relationship(
        "Participant", back_populates="messages"
    )


class AgentFile(Base):
    """A single file in an agent's per-agent directory manifest.

    Each row represents one file under ``~/.doorae/agents/<agent_id>/``
    that the server wants the machine to materialize on spawn. ``path``
    is a whitelisted relative path (see ``doorae.agent_files`` for the
    rules); ``content`` is the full text body. On spawn, all rows for
    the agent are packed into ``SpawnAgentFrame.files`` and the machine
    reconciles its local directory against that manifest (files absent
    from the manifest are pruned).
    """

    __tablename__ = "agent_files"
    __table_args__ = (
        UniqueConstraint("agent_id", "path", name="uq_agent_files_agent_path"),
        Index("ix_agent_files_agent", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    agent: Mapped["Agent"] = relationship("Agent", back_populates="files")


class SavedMessage(Base):
    """A user's bookmarked message."""

    __tablename__ = "saved_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "message_id", name="uq_saved_user_message"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ActivityLog(Base):
    """Agent lifecycle activity events."""

    __tablename__ = "activity_logs"
    __table_args__ = (
        Index("ix_activity_logs_agent_ts", "agent_id", "timestamp"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=None)


class MachineActivityLog(Base):
    """Machine lifecycle activity events (online, offline, drain)."""

    __tablename__ = "machine_activity_logs"
    __table_args__ = (
        Index("ix_machine_activity_logs_machine_ts", "machine_id", "timestamp"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    machine_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=None)


class Task(Base):
    """A task associated with a room."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_room_status", "room_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="todo")
    assignee_participant_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("participants.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
