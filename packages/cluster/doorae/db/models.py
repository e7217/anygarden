"""SQLAlchemy ORM models for the Doorae chat server."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Float,
    text as sa_text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from doorae.db.types import UtcDateTime


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
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)

    # ``passive_deletes=True`` defers child cleanup to the FK's
    # ``ON DELETE CASCADE`` — without it, SA tries to UPDATE
    # rooms.project_id to NULL before the cascade fires, which
    # violates NOT NULL. Same rationale as ``Room.participants``.
    rooms: Mapped[list["Room"]] = relationship(
        "Room",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


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
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)

    project: Mapped["Project"] = relationship("Project", back_populates="rooms")
    parent_room: Mapped[Optional["Room"]] = relationship(
        "Room", remote_side="Room.id", back_populates="child_rooms"
    )
    child_rooms: Mapped[list["Room"]] = relationship("Room", back_populates="parent_room")
    # passive_deletes defers to the FK's ON DELETE CASCADE — without
    # it the ORM tries to UPDATE the child's room_id to NULL before
    # cascade fires, which violates NOT NULL. Same pattern as the
    # Machine.engines / Machine.tokens relationships.
    participants: Mapped[list["Participant"]] = relationship(
        "Participant",
        back_populates="room",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="room",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    representative_agent: Mapped[Optional["Agent"]] = relationship(
        "Agent", foreign_keys=[representative_agent_id]
    )


class User(Base):
    __tablename__ = "users"
    # ``email`` is UNIQUE only when present. Anonymous guests (see
    # §11 design doc) have no email, and the registered-user path
    # should still reject duplicates. A partial unique index works on
    # both SQLite (3.8.0+) and PostgreSQL — the migration file creates
    # the same index at deploy time.
    __table_args__ = (
        Index(
            "ux_users_email_not_null",
            "email",
            unique=True,
            sqlite_where=sa_text("email IS NOT NULL"),
            postgresql_where=sa_text("email IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Registered users: email + password_hash required. Guests: both NULL.
    # DB-level uniqueness handled by ``ux_users_email_not_null``.
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Guest marker. True means the row came from ``POST /auth/guest``
    # and is bound to a single room via the JWT's ``room_id`` claim.
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Guest-supplied display name (host UI can show a badge). Opaque
    # to the server beyond length limits — enforcement lives in the
    # auth handler, not the DB.
    display_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


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
        UtcDateTime, nullable=True, default=None
    )
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    last_crash_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    reasoning_effort: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default=None
    )
    # Engine-specific model id (e.g. "gpt-5.4-mini"). None means the
    # adapter's built-in default is used. See doorae.engines.catalog
    # for supported ids per engine.
    model: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, default=None
    )
    restart_policy: Mapped[str] = mapped_column(String(64), default="restart_anywhere")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    max_restarts: Mapped[int] = mapped_column(Integer, default=3)
    restart_window_seconds: Mapped[int] = mapped_column(Integer, default=300)
    # Issue #73 — which runtime (machine-side process) hosts this
    # agent. ``"python"`` spawns ``doorae-agent``; ``"typescript"``
    # spawns ``doorae-agent-ts``. Defaults to ``"python"`` so rows
    # created before the schema migration continue to use the Python
    # runtime. ``server_default`` is the load-bearing piece — without
    # it the SQLite batch migration refuses to add a NOT NULL column.
    runtime: Mapped[str] = mapped_column(
        String(20), nullable=False, default="python", server_default="python"
    )
    # Issue #101 — admin-customizable avatar. ``avatar_kind`` picks the
    # renderer branch (``'emoji'``, ``'lucide'``, or NULL for the default
    # seed-driven initial); ``avatar_value`` carries the payload (the
    # emoji character or the lucide icon component name). Both NULL is
    # the "no customization" state and is how every pre-#101 agent
    # loads post-migration.
    avatar_kind: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, default=None
    )
    avatar_value: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)

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
        UtcDateTime, nullable=True, default=None
    )
    cpu_cores: Mapped[int] = mapped_column(Integer, default=0)
    memory_gb: Mapped[float] = mapped_column(Float, default=0.0)
    # Placement capacity limit. Hidden from UI/API/CLI as of 2026-04-15
    # (issue #2) — kept in the schema so ``placement.py`` can still
    # enforce a soft cap and we can re-expose it later without a
    # migration. Set absurdly high so it never bites in practice.
    max_agents: Mapped[int] = mapped_column(Integer, default=1000)
    labels: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)

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
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)

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
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
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
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )

    agent: Mapped["Agent"] = relationship("Agent")


class RoomInviteLink(Base):
    """Shareable invite token that lets an anonymous guest join a room.

    Design doc: §11.3. Tokens follow the ``AgentToken`` shape —
    plaintext is generated once (``inv_<urlsafe>``), only the argon2
    hash plus a 12-char ``lookup_hint`` are stored. Validation happens
    in ``POST /auth/guest`` (PR C); this table carries issue/list/
    revoke state only.
    """

    __tablename__ = "room_invite_links"
    __table_args__ = (
        Index("ix_room_invite_links_room", "room_id"),
        Index("ix_room_invite_links_hint", "lookup_hint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    lookup_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    # None = no expiry. Checked only at acceptance time in PR C.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    # Non-null ⇒ admin called DELETE /invites/{id}. Accepting this
    # invite is rejected regardless of ``expires_at``/``use_count``.
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    # None = unlimited uses. Acceptance increments ``use_count`` and
    # refuses when ``use_count >= max_uses``.
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    room: Mapped["Room"] = relationship("Room")
    created_by: Mapped["User"] = relationship("User")


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
    joined_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    # Sidebar pin state — user can drag a room to a top "pinned"
    # section in the sidebar and reorder within it. ``pinned=False``
    # keeps the room in the default (alphabetical) section; ``True``
    # promotes it to the pinned section ordered by ``sort_order``
    # ascending. Stored per-Participant so each user's order is
    # independent. ``sort_order`` uses sparse integer spacing (1024
    # apart) so mid-list reorders don't have to renumber the whole
    # list — see ``rooms.service.reorder_pinned_rooms``.
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None
    )

    room: Mapped["Room"] = relationship("Room", back_populates="participants")
    user: Mapped[Optional["User"]] = relationship("User")
    agent: Mapped[Optional["Agent"]] = relationship("Agent")
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="participant",
        passive_deletes=True,
    )

    __table_args__ = (
        # Accelerates the "load my pinned rooms in order" query that
        # the sidebar runs on every boot and after every reorder.
        Index(
            "ix_participants_user_pinned_order",
            "user_id",
            "pinned",
            "sort_order",
        ),
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
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)

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
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )

    agent: Mapped["Agent"] = relationship("Agent", back_populates="files")


class SkillLibraryEntry(Base):
    """A shared skill registered from a GitHub repo (#119 Phase 1).

    One row per (source, name, pinned_rev). ``source`` is a
    ``"owner/repo"`` slug (e.g. ``"vercel-labs/agent-skills"``);
    ``name`` is the skill directory under ``skills/``; ``pinned_rev``
    is the commit SHA resolved at registration time so that spawning
    an agent never depends on upstream being reachable.

    Phase 1 only materializes ``skill_md`` (the SKILL.md body) —
    ``extra_files`` and ``scripts_detected`` stay as empty / metadata
    JSON until Phase 3 flips on the full directory passthrough. The
    columns ship now so Phase 3 is a code-only change.
    """

    __tablename__ = "skill_library"
    __table_args__ = (
        UniqueConstraint(
            "source", "name", "pinned_rev",
            name="uq_skill_library_source_name_rev",
        ),
        Index("ix_skill_library_source_name", "source", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    pinned_rev: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_md: Mapped[str] = mapped_column(Text, nullable=False)
    # Phase 3 will populate ``extra_files`` with ``{rel_path: body}``;
    # in Phase 1 it's always ``{}``. Default=dict keeps ORM writes
    # consistent with the migration's NOT NULL constraint.
    extra_files: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Non-SKILL.md paths the GitHub tree returned at registration.
    # Pure UI metadata in Phase 1 so admins can see "this skill ships
    # extra files we didn't materialize".
    scripts_detected: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Phase 2 will wire approval. NULL in Phase 1 — spawner ignores
    # this column until the approval gate lands.
    approved_by: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, default=None
    )
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


class AgentSkill(Base):
    """M:N link between an Agent and a SkillLibraryEntry (#119 Phase 1).

    Splitting attachment into a link table (rather than stamping a
    JSON array on Agent) lets one skill fan out to many agents
    without duplicating its body, and lets the spawner resolve
    ``agent.skills`` by a single join instead of parsing JSON.
    """

    __tablename__ = "agent_skills"
    __table_args__ = (
        Index("ix_agent_skills_skill", "skill_library_id"),
    )

    agent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    skill_library_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("skill_library.id", ondelete="CASCADE"),
        primary_key=True,
    )
    attached_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


class MCPServerTemplate(Base):
    """An MCP server definition (#124).

    One row per server template (github / slack / notion / any
    admin-authored custom server). ``config_per_engine`` is the
    engine-native config body keyed by engine id — the cluster does
    NOT translate between engines; builtin definitions include one
    entry per supported engine and custom templates author each engine
    block explicitly. This keeps the representation faithful to the
    format each CLI actually reads.

    ``source`` splits the table into ``"builtin"`` (re-seeded at
    startup via ``seed_builtins``) and ``"custom"`` (authored via the
    admin API). The split matters for mutation rules — the service
    layer refuses to PUT/DELETE builtin rows so an admin can't
    accidentally break a well-known server config. ``created_by`` is
    NULL on builtins and is the admin user id on customs.
    """

    __tablename__ = "mcp_server_templates"
    __table_args__ = (
        UniqueConstraint("name", name="uq_mcp_server_templates_name"),
        Index("ix_mcp_server_templates_source", "source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None
    )
    icon: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, default=None)
    config_per_engine: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    required_env_vars: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    supported_engines: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="custom")
    created_by: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )


class MCPServerInstance(Base):
    """A per-agent attachment of an MCP server template (#124).

    Holds the Fernet-encrypted env values in ``env_values_encrypted``
    (nullable — some templates like ``filesystem`` require no
    credentials). The ``(template_id, agent_id)`` uniqueness
    constraint enforces "one instance per template per agent"; the
    service's attach path upserts so re-submitting credentials
    overwrites in place instead of creating duplicates.

    ``enabled=False`` lets admins temporarily disable a server
    without losing the stored credentials — the render path skips
    disabled rows so the agent's settings file is stripped of that
    server on its next spawn.
    """

    __tablename__ = "mcp_server_instances"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "agent_id",
            name="uq_mcp_server_instances_template_agent",
        ),
        Index("ix_mcp_server_instances_agent", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    template_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("mcp_server_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    env_values_encrypted: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True, default=None
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )


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
    saved_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


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
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
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
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
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
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
