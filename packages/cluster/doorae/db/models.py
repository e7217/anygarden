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
    # #179 — nullable so agent DM rooms aren't tied to any project's
    # lifecycle. Regular rooms still require ``project_id`` — enforced at
    # the API layer (see ``RoomCreate`` in ``rooms/router.py``). DM rooms
    # created from the agent-creation path use ``project_id=NULL``, which
    # bypasses the ``ON DELETE CASCADE`` entirely (cascade only fires when
    # the FK's value matches a deleted row).
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
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
    # Issue #148 — per-room ambient context window toggle. When True
    # the WS broadcast path stamps ``metadata.ingest_only=True`` on
    # messages that aren't directly addressed to anyone, letting
    # peer agents ingest the text as background context without
    # triggering a full response. Part 1 stores the flag; Part 3
    # wires the broadcast-side logic.
    #
    # Issue #225 — default flipped to True (migration 028). Fresh
    # rooms opt into ambient sharing by default; admins can still
    # toggle individual rooms off via the room edit dialog (the
    # PATCH field is now admin-only, matching ``speaker_strategy``).
    context_window_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_text("1")
    )
    # Issue #159 Phase A — speaker-selection strategy for the room.
    # ``mentioned_only`` (default) preserves the current decide_policy
    # behaviour; ``round_robin`` and ``orchestrator`` are activated in
    # Phase B/C. The value feeds ``decide_policy``'s strategy
    # dispatcher via the welcome frame.
    speaker_strategy: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="mentioned_only",
        server_default=sa_text("'mentioned_only'"),
    )
    # Issue #159 Phase A — the agent that drives handoffs when
    # ``speaker_strategy='orchestrator'``. Kept separate from
    # ``representative_agent_id`` (cross-room query role) so the two
    # responsibilities stay legible; the same Agent can hold both.
    orchestrator_agent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    # Issue #159 Phase A — the participant the orchestrator most
    # recently handed off to. Read by the orchestrator branch of
    # decide_policy to decide who RESPONDs next.
    next_speaker_participant_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("participants.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    # Issue #159 Phase A — round-robin cursor. Advances per-turn in
    # the ``round_robin`` strategy; unused by other strategies.
    current_speaker_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
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
    # ``foreign_keys`` is load-bearing after #159 Phase A — adding
    # ``Room.next_speaker_participant_id`` introduced a second FK
    # between rooms and participants, so SQLAlchemy can no longer
    # infer the join condition for this collection implicitly.
    participants: Mapped[list["Participant"]] = relationship(
        "Participant",
        back_populates="room",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="Participant.room_id",
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
    # Issue #148 Part 2 — agent-side opt-out from ambient context
    # window broadcasts. When True the agent skips ``ingest_only``
    # messages even if the containing room has the window enabled.
    # Part 2 stores the flag and exposes it on the REST API; Part 3
    # wires it into ``decide_policy``.
    context_window_opt_out: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("0")
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

    # ``foreign_keys`` disambiguates the join after #159 Phase A
    # introduced ``Room.next_speaker_participant_id`` — otherwise
    # SQLAlchemy can't tell which FK column carries the relationship.
    room: Mapped["Room"] = relationship(
        "Room",
        back_populates="participants",
        foreign_keys=[room_id],
    )
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
        Index("ix_skill_library_created_by_agent", "created_by_agent_id"),
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
    # Phase 2 (#125) wires the approval gate. ``approved_by`` holds the
    # admin user id; NULL means the skill is still pending review and
    # ``resolve_for_agent`` / the attach endpoint both refuse it.
    # ``approved_at`` is the companion timestamp — stored separately so
    # the approval moment survives the SET NULL on user delete.
    approved_by: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, default=None
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    # #120 — agent-authored skills carry the author's agent id here;
    # shared / admin-registered rows keep this NULL.  The
    # ``resolve_for_agent`` query ORs on this column so an author
    # always sees their own skills regardless of approval state, and
    # the admin ``promote`` endpoint flips it back to NULL to move
    # the row into the shared library.
    created_by_agent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
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


class SkillLibraryAudit(Base):
    """Append-only audit log for skill library state changes (#125 Phase 2).

    Every register / approve / reject / delete / update / attach /
    detach operation lands a row here so administrators have a
    reviewable trail of "who did what to this skill". Rows are never
    updated or deleted by the application — the FKs use ``SET NULL``
    on delete so audit entries survive even after the referenced
    skill or actor is purged, which is the whole point of an audit
    trail (you want to know *that* a deletion happened, even after the
    skill row itself is gone).

    ``action`` is a free-form ``String(32)`` rather than a DB enum so
    adding new actions (Phase 5 stale-check re-approvals, etc.) is a
    code-only change. ``detail`` is JSON so the schema can evolve per
    action type without requiring a migration — the service layer
    chooses the shape per call site (see ``020_skill_approve_and_audit``
    migration docstring for the current conventions).
    """

    __tablename__ = "skill_library_audits"
    __table_args__ = (
        Index("ix_skill_library_audits_skill_at", "skill_library_id", "at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    skill_library_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("skill_library.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


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
        Index("ix_activity_logs_request", "request_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    request_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, default=None
    )
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


# ── LLM Gateway (#197) ─────────────────────────────────────────────────
#
# A LiteLLM subprocess supervised by doorae-server routes every agent
# LLM call through `/api/v1/llm/*`. These three tables back the admin
# CRUD surface and usage telemetry. See docs/design/12-llm-gateway.md
# and docs/decisions/004-embedded-litellm-gateway.md for rationale.


class LLMGatewayModel(Base):
    """One entry in the gateway's ``model_list`` (config.yaml).

    Admin-managed. Each row renders to a single ``litellm_params`` block
    when the config writer serialises the DB state. Secrets never land
    in the rendered yaml — only a reference (``api_key_ref``) to the
    ``LLMGatewaySecret`` row whose decrypted value is injected into the
    LiteLLM subprocess env at spawn time.
    """

    __tablename__ = "llm_gateway_models"
    __table_args__ = (
        UniqueConstraint("model_name", name="uq_llm_gateway_models_name"),
        Index("ix_llm_gateway_models_provider", "provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # User-facing identifier ("claude-sonnet-4-6"). Unique within the
    # gateway — an agent's ``model`` request maps to exactly one row.
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # "anthropic" / "openai" / "bedrock" / "vertex" / "azure" / "ollama" /
    # "custom". Used by the UI for grouping and preset prefill only; the
    # actual routing is determined by ``upstream_model``.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # LiteLLM-native routing identifier ("anthropic/claude-sonnet-4-6",
    # "openai/gpt-5.4", "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0").
    upstream_model: Mapped[str] = mapped_column(String(255), nullable=False)
    # The env var name (not value!) LiteLLM should read for this model's
    # credentials. Matches the PK of a ``LLMGatewaySecret`` row. The
    # config writer emits ``api_key: os.environ/DOORAE_LITELLM_<ref>`` and
    # the supervisor injects ``DOORAE_LITELLM_<ref>=<decrypted>`` at spawn.
    api_key_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional extras passed through to ``litellm_params`` verbatim —
    # temperature, max_tokens, custom headers, etc. JSON dict.
    extra_params: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )


class LLMGatewaySecret(Base):
    """Encrypted API key for a LiteLLM upstream provider.

    Stored separately from ``LLMGatewayModel`` so one secret can back
    multiple models (e.g. two Anthropic models sharing one key), and so
    rotating a key does not touch model rows. Ciphertext is opaque
    bytes produced by the existing ``MCPSecrets`` Fernet — reusing the
    operator-managed ``DOORAE_MCP_SECRETS_KEY`` keeps KMS surface a
    single key to rotate.

    ``env_var_name`` is the natural PK (matches ``api_key_ref`` on model
    rows) so a model row lookup does not need to carry an extra foreign
    key column.
    """

    __tablename__ = "llm_gateway_secrets"

    env_var_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    # "ok" / "invalid" / "timeout" / "error:<short>". Free-form string so
    # the UI can render the raw status without an enum migration every
    # time a new failure mode appears.
    last_test_status: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )


class LLMGatewayUsage(Base):
    """One row per LLM request relayed through ``/api/v1/llm/*``.

    Written by the reverse-proxy layer after the response completes
    (streaming or not). A 30-day TTL cron prunes stale rows so the
    table stays bounded. Admin UI's Usage section queries this via
    ``GROUP BY`` at read time — on-the-fly aggregation is cheap enough
    for the initial scale and keeps per-request detail available for
    debugging.

    ``identity_kind`` / ``identity_id`` capture the *caller* rather than
    tying to ``agent_id`` only, because user and machine tokens can
    also hit the proxy (admin test pings, machine-scoped calls).
    When the caller is an agent, ``agent_id`` is populated for the
    common-case aggregation path; ``identity_id`` always reflects the
    same value so queries that don't need the agent join work too.
    """

    __tablename__ = "llm_gateway_usage"
    __table_args__ = (
        Index("ix_llm_gateway_usage_timestamp", "timestamp"),
        Index("ix_llm_gateway_usage_agent_ts", "agent_id", "timestamp"),
        Index("ix_llm_gateway_usage_model_ts", "model_name", "timestamp"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    # Nullable so user/machine callers (admin test pings etc.) don't
    # require a fake agent row. Populated for agent-initiated calls.
    agent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    room_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("rooms.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    # "agent" / "user" / "machine". Matches ``auth.Identity.kind``.
    identity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Raw identity id from the auth layer (agent_id / user_id /
    # machine_id depending on kind). Kept as a plain string — no FK so
    # a deleted caller's history is preserved.
    identity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    # Populated only on non-2xx. Short message from upstream or proxy
    # for debug views; never user-facing.
    error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, default=None)
