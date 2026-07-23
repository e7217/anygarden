"""SQLAlchemy ORM models for the Anygarden chat server."""

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
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Float,
    text as sa_text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from anygarden.db.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base for all Anygarden models."""
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
    # Issue #237 — ephemeral / "temporary session" flag. When True the
    # agent is instructed via system_prompt to skip writing to
    # ``memory/notes.md``. This is a trust-model signal, not a hard
    # filesystem guard (see plan §3.2 decision 3). Toggle-able by DM
    # owner; admin-only for non-DM rooms.
    ephemeral: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("0")
    )
    # Issue #266 — opt-in toggle that controls whether human
    # participants are eligible task assignees in this room. Default
    # False so existing rooms behave like before (agent-only target,
    # which matches the "에이전트가 메인" stance). Flipped on per-room
    # by admins when human-assignment workflows are wanted.
    allow_human_assignment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("0")
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
        # #516 — admin query "which agents are unavailable, and why".
        Index("ix_agents_unavailable_code", "unavailable_code"),
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
    # materializes this into ~/.anygarden/agents/<id>/AGENTS.md on spawn.
    # See docs/plans/2026-04-11-per-agent-directory-skills.md
    agents_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    last_crash_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    # #516 — structured "why can't this agent respond" for the not-running
    # family (engine change, no machine for engine, spawn failure, crash,
    # engine drift, no room). NULL ``unavailable_code`` == the agent is fine.
    # The human message is NOT stored — it is derived from ``(code, detail,
    # audience)`` via ``anygarden.agent_availability.render_unavailable_message``
    # so it stays translatable and audience-gated (stderr never leaks to
    # end users). ``unavailable_detail`` carries engine name / stderr_tail /
    # exit_code / running-vs-db engine. NULL-default so pre-#516 rows need no
    # backfill, mirroring ``permission_level`` / ``turn_timeout_sec``.
    unavailable_code: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, default=None
    )
    unavailable_detail: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, default=None
    )
    unavailable_since: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    reasoning_effort: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default=None
    )
    # Engine-specific model id (e.g. "gpt-5.4-mini"). None means the
    # adapter's built-in default is used. See anygarden.engines.catalog
    # for supported ids per engine.
    model: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, default=None
    )
    # Issue #309 — semantic permission tier translated by each engine
    # adapter into native dials. Values: "restricted" | "standard"
    # (default behaviour) | "trusted" (host access). NULL means
    # "fall back to the standard tier" — chosen so pre-#309 rows
    # stay byte-identical without a backfill migration. See
    # ``anygarden_agent.integrations.codex_cli._resolve_codex_flags`` for the
    # codex translation; gemini-cli + claude-code mappings land in
    # the follow-up PR (#309 PR-B).
    permission_level: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default=None
    )
    # Issue #493 — per-agent turn timeout in seconds. NULL means fall back
    # to the global env / hardcoded engine default (see
    # ``anygarden_agent.integrations._turn_timeout``). The cluster API
    # validates the range so it stays below the orphan-sweep threshold
    # (``ANYGARDEN_REQUEST_LIVENESS_SEC``); chosen NULL-default so pre-#493
    # rows need no backfill, mirroring ``permission_level``.
    turn_timeout_sec: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None
    )
    restart_policy: Mapped[str] = mapped_column(String(64), default="restart_anywhere")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    max_restarts: Mapped[int] = mapped_column(Integer, default=3)
    restart_window_seconds: Mapped[int] = mapped_column(Integer, default=300)
    # Issue #73 — which runtime (machine-side process) hosts this
    # agent. ``"python"`` spawns ``anygarden-agent``; ``"typescript"``
    # spawns ``anygarden-agent-ts``. Defaults to ``"python"`` so rows
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
    # Issue #237 — per-agent long-term memory scratchpad (markdown).
    # DB is the "last-known snapshot"; runtime truth lives in the file
    # ``~/.anygarden/agents/<id>/memory/notes.md`` on the hosting machine.
    # Spawner materializes this into the file on start; machine flushes
    # file -> DB on heartbeat and graceful shutdown. See plan §3.2
    # decision 4 for the bi-directional sync rationale.
    memory_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    # Issue #271 — short public-facing self-introduction surfaced to other
    # participants (LLM roster + mention popover + participant list).
    # Distinct from ``agents_md`` which is *self-directed* (the agent's
    # own system prompt body); ``description`` is what *others* see.
    # Application layer caps this at 200 chars; DB stays Text for
    # forward flexibility.
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    # Issue #279 — per-agent collaboration policy. ``solo`` (default)
    # preserves pre-#279 behaviour: the agent answers within its own
    # turn. ``collaborative`` triggers a server-supplied hint suffix in
    # the LLM system prompt instructing the agent to peer-mention
    # teammates and synthesize their replies. Stored as a small string
    # rather than an enum type so cross-DB (sqlite/postgres) batch
    # migrations stay simple.
    collaboration_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="solo", server_default=sa_text("'solo'")
    )
    # Issue #455 (reliability Wave 2a) — why this agent is currently
    # arrested. NULL is the normal "not paused for a special reason"
    # state, which is what every pre-#455 row loads as (the column is
    # nullable with no server default — no backfill needed). The only
    # value the runtime sets today is ``'budget'``: the active-stop path
    # flips this when a hard-stop AGENT-scope token budget is exceeded,
    # and the invocation-block gate short-circuits a paused agent's
    # residual LLM calls before paying for the window SUM. Admin resume
    # clears it back to NULL. Distinct from ``desired_state == 'stopped'``
    # because operators need to tell a *budget* arrest apart from an
    # ordinary stop.
    pause_reason: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default=None
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
    # ``hostname`` is the daemon-detected real hostname (socket.gethostname()),
    # overwritten on every register frame. Empty until the daemon first
    # connects. NOT the user-facing label — that is ``name`` (identifier) and
    # ``description`` (free-form note) as of issue #523.
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    # User-supplied free-form label / note (issue #523). Optional; replaces
    # the former user-entered ``hostname`` input which is now auto-detected.
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, default=None)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="offline")
    daemon_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)
    daemon_last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    # Static system info reported by the daemon on register (issue #523).
    # ``cpu_cores`` / ``memory_gb`` predate #523 but were never populated;
    # #523 wires the daemon to fill them. ``lan_ip`` / ``os_platform`` are new.
    cpu_cores: Mapped[int] = mapped_column(Integer, default=0)
    memory_gb: Mapped[float] = mapped_column(Float, default=0.0)
    lan_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)
    os_platform: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, default=None)
    # #550 — server-driven self-update state, surfaced in the admin machine
    # view. ``updating`` (self_update frame sent) → ``success`` (new
    # daemon_version confirmed on re-register) / ``failed`` (daemon reported
    # failure). NULL means no update has ever been triggered.
    update_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)
    update_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, default=None)
    update_started_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
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
    # Sidebar unread-update marker (#385). Stores the highest
    # room-local message seq this user has seen. NULL means the
    # room has never been marked read by this participant.
    last_read_message_seq: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, default=None
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
        # A user (or agent) must appear at most once per room. Without
        # this guard duplicate rows crept in via non-idempotent add
        # paths, and ``require_room_member``'s single-row fetch then
        # 500'd/4003'd the whole room (#519). Partial (``… IS NOT NULL``)
        # because ``user_id`` and ``agent_id`` are mutually-exclusive
        # nullable columns — a plain composite UNIQUE would let SQLite's
        # NULL-distinct rule wave every duplicate through. Migration 052
        # dedupes existing rows and builds the same indexes at deploy.
        Index(
            "uq_participants_room_user",
            "room_id",
            "user_id",
            unique=True,
            sqlite_where=sa_text("user_id IS NOT NULL"),
            postgresql_where=sa_text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_participants_room_agent",
            "room_id",
            "agent_id",
            unique=True,
            sqlite_where=sa_text("agent_id IS NOT NULL"),
            postgresql_where=sa_text("agent_id IS NOT NULL"),
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


class RoomSharedFile(Base):
    """A file attached to a room and copy-distributed to every
    participating agent's ``memory/shared/`` directory (#246).

    The original bytes live on disk under
    ``<settings.room_files_dir>/<room_id>/<id>`` so SQLite stays
    compact. This row keeps metadata plus ``sha256`` for integrity
    checks and idempotent fan-out.
    """

    __tablename__ = "room_shared_files"
    __table_args__ = (
        UniqueConstraint("room_id", "storage_name", name="uq_room_shared_storage"),
    )

    # ``id`` doubles as the on-disk filename under
    # ``<room_files_dir>/<room_id>/<id>``. uuid keeps user-supplied
    # filenames out of the filesystem path (defence against
    # traversal / OS-specific name restrictions).
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    # Original filename as uploaded — shown in the UI, never touched
    # by the filesystem layer.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Sanitized name used on the agent side under
    # ``memory/shared/<storage_name>``. Derived from ``filename`` but
    # scrubbed of path separators / reserved names.
    storage_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Path relative to ``settings.room_files_dir`` (``<room_id>/<id>``).
    # Stored redundantly with ``id``/``room_id`` so future relocations
    # (e.g. sharding across disks) don't require back-computing paths.
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    # ``SET NULL`` so that deleting an uploader doesn't cascade-delete
    # the file row — the file belongs to the room, not the person who
    # happened to upload it.
    uploaded_by: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)

    room: Mapped["Room"] = relationship("Room")


class RoomArtifact(Base):
    """A file produced by an agent and surfaced in the room's right-hand
    artifact panel (#290 Phase B).

    Distinct from :class:`RoomSharedFile`: that flow is user-uploaded
    text consumed by agents (system-prompt input). This flow is the
    inverse — the agent drops a file under
    ``<agent_root>/memory/outbox/`` and the machine daemon polls the
    directory, sha256s each file, and ships the bytes to the server
    via ``RoomArtifactProducedFrame``. The server stores the bytes
    under ``settings.artifact_files_dir/<room_id>/<id>`` and keeps
    metadata + sha256 here.

    Re-delivery is idempotent thanks to the ``(room_id, sha256)``
    unique constraint — the daemon may re-emit the same file on
    reconnect/backfill and the server treats the duplicate as a
    no-op without per-server bookkeeping.
    """

    __tablename__ = "room_artifacts"
    __table_args__ = (
        UniqueConstraint("room_id", "sha256", name="uq_room_artifact_sha"),
        Index("ix_room_artifacts_room_id", "room_id"),
    )

    # ``id`` doubles as the on-disk filename under
    # ``<artifact_files_dir>/<room_id>/<id>``. uuid keeps user-supplied
    # filenames out of the filesystem path.
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    # ``SET NULL`` so deleting the producer doesn't cascade-delete its
    # past artifacts — they belong to the room, not the agent that
    # happened to drop them.
    produced_by_agent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    # Display name as authored by the agent (basename of the outbox
    # file). Sanitised on the daemon side before the wire frame.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Path relative to ``settings.artifact_files_dir`` (``<room_id>/<id>``).
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)

    room: Mapped["Room"] = relationship("Room")


class AgentFile(Base):
    """A single file in an agent's per-agent directory manifest.

    Each row represents one file under ``~/.anygarden/agents/<agent_id>/``
    that the server wants the machine to materialize on spawn. ``path``
    is a whitelisted relative path (see ``anygarden.agent_files`` for the
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
        # #427 — per-room activity timelines (the /rooms/{id}/activity
        # endpoint) query by room; an index keeps that off a full scan.
        Index("ix_activity_logs_room_ts", "room_id", "timestamp"),
        # #447 — turn outcome / engine promoted out of ``details`` to
        # first-class indexed columns so the reaper and outcome-filtered
        # activity queries don't full-scan + json_extract.
        Index("ix_activity_logs_outcome_ts", "outcome", "timestamp"),
        Index("ix_activity_logs_room_outcome", "room_id", "outcome"),
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
    # #427 — promoted out of ``details`` JSON to a first-class indexed
    # column so per-room timelines don't need a full-scan + json_extract.
    # Nullable: system events (start/stop/state_changed) and pre-#427
    # rows have no room.
    room_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, default=None
    )
    # #447 — turn outcome (ok/failed/timeout/cancelled/rejected) and the
    # engine that ran it, promoted out of ``details`` JSON to first-class
    # indexed columns. Nullable: system events (start/stop/state_changed)
    # and pre-#447 rows carry no outcome/engine; forward-only (no
    # backfill — legacy ``details`` has no consistent signal).
    outcome: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default=None
    )
    engine: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default=None
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
    """A task associated with a room.

    Issue #302 (Phase 2) — extended to absorb scheduled "Goal runs" as
    rows of the same table. Manual tasks have ``goal_id IS NULL``;
    Goal-derived tasks carry the link plus a snapshot of the Goal
    spec at trigger time + execution metadata. The legacy columns
    (``room_id`` / ``title`` / ``status`` / ``assignee_participant_id``)
    keep their semantics so #266 dual-view queries are unaffected.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_room_status", "room_id", "status"),
        # Issue #266 — accelerates the per-agent aggregation query
        # backing ``GET /api/v1/agents/{id}/tasks``. The 1차 view (룸)
        # is covered by ``ix_tasks_room_status``; this is its dual for
        # the 2차 view (에이전트 프로필).
        Index("ix_tasks_assignee_status", "assignee_participant_id", "status"),
        # Issue #302 — Goal detail view's "recent runs" panel issues
        # ``WHERE goal_id = ? ORDER BY created_at DESC LIMIT N``. The
        # composite keeps that scan cheap even when a high-frequency
        # Goal accumulates thousands of rows. Wave 1b (#449) also reuses
        # it for the in-flight dedup probe (``WHERE goal_id = ? AND
        # status IN ('todo','in_progress')``).
        Index("ix_tasks_goal_created", "goal_id", "created_at"),
        # Issue #449 (Wave 1b) — exactly-once goal firing. The
        # scheduler CAS claim + Run-now both stamp a deterministic
        # ``idempotency_key``; this UNIQUE index makes a duplicate fire
        # of the same slot raise IntegrityError instead of creating a
        # second Task. NULL is multi-allowed on both SQLite and
        # Postgres so non-goal Tasks (key NULL) never collide.
        UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
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
    assigned_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    """Timestamp the assignee was first attached (or last reassigned).
    Used by the goal-scheduler sweeper (#314) to detect pickup-timeout
    on assigned-but-never-started tasks. NULL when the task has no
    assignee. Updated whenever ``assignee_participant_id`` transitions
    NULL → not-NULL or value → different value."""

    # ── Issue #302 (Phase 2) — Goal-derived task fields ────────────
    # All nullable + sensible defaults so the migration leaves
    # pre-existing manual rows untouched.
    goal_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("agent_goals.id", ondelete="SET NULL"), nullable=True
    )
    """Link back to the originating Goal. NULL for manual tasks."""

    triggered_by: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual"
    )
    """How the task came to exist — ``manual`` (user / agent created it)
    or ``scheduler`` (a Goal trigger fired). Future values: ``webhook``,
    ``orchestrator``."""

    spec: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    """Snapshot of the Goal's spec at trigger time. We snapshot rather
    than join so editing a Goal mid-run doesn't retroactively rewrite
    history. NULL for manual tasks."""

    started_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    agent_session_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, default=None
    )
    tokens_used: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None
    )
    result_markdown: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None
    )
    is_interesting: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    """Used by ``materialize='interesting_only'`` Goals to decide
    whether a successful run still earns a Task row. Errors are
    auto-flagged interesting; agents may opt successes in via the
    ``[INTERESTING]`` marker (Phase 3)."""

    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, default=None
    )
    """Issue #449 (Wave 1b) — deterministic dedup token for goal fires.
    Stamped by ``trigger_goal`` as ``f"{goal_id}:{slot_epoch}"`` for a
    scheduled fire (slot = the due ``next_run_at`` instant), or
    ``f"{goal_id}:manual:{minute_bucket}"`` for a Run-now on a manual
    goal. Backed by the ``uq_tasks_idempotency_key`` UNIQUE index so a
    duplicate fire of the same slot raises IntegrityError. NULL for
    manual / non-goal Tasks (multiple NULLs are allowed under a
    nullable UNIQUE on both SQLite and Postgres)."""


class TaskBlocker(Base):
    """A first-class "this task is blocked by that task" dependency edge
    (#459, reliability Wave 2c).

    Before this relation a blocked Task carried no record of *what* it was
    waiting on, so when its blocker finished the dependent stayed inert
    until the goals sweeper timed it out or a human re-routed it manually.
    A blocker row links ``task_id`` (the dependent that is waiting) to
    ``blocked_by_task_id`` (the prerequisite that must reach a terminal
    status first). A Task may be blocked by many others (many-to-many),
    which is why this is a relation table rather than a single
    ``blocked_by`` column on :class:`Task`.

    When ``blocked_by_task_id`` transitions to ``done`` / ``failed`` the
    resolve-wake hook (``mark_task_status`` + ``api/v1/tasks.update_task``)
    deletes the satisfied edge and, once *all* of the dependent's blockers
    are terminal, returns the dependent to ``todo`` and re-injects a
    ``task_assignment`` mention so its assignee agent wakes up again.
    """

    __tablename__ = "task_blockers"
    __table_args__ = (
        # Composite PK = the edge identity. A duplicate (task_id,
        # blocked_by_task_id) insert raises IntegrityError, which the
        # ``add_task_blocker`` handler treats as idempotent success.
        PrimaryKeyConstraint(
            "task_id", "blocked_by_task_id", name="pk_task_blockers"
        ),
        # The resolve-wake reverse lookup is ``WHERE blocked_by_task_id =
        # :just_completed``; the PK's leading column is ``task_id`` so it
        # can't serve that scan. A dedicated index keeps it cheap.
        Index("ix_task_blockers_blocked_by", "blocked_by_task_id"),
    )

    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The dependent task that is waiting (the one to wake when cleared)."""

    blocked_by_task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The prerequisite task that must finish first."""

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)

    # CASCADE on both FKs so deleting either endpoint task tears down its
    # blocker edges automatically — matches how the rest of this codebase
    # leans on ``ondelete='CASCADE'`` (rooms→tasks, agents→tokens) rather
    # than app-level cleanup, and avoids dangling edges that would make the
    # cycle-guard walk reference vanished tasks.


class AgentTurnTask(Base):
    """The ``request_id`` ↔ ``task_id`` correlation for an assignment turn
    (#463, reliability Wave 2 — lifecycle→Task re-dispatch bridge).

    An *assignment-originated* turn is one woken by a synthetic
    ``[TASK]`` mention injected through
    :func:`anygarden.messages.service.inject_task_assignment_message`
    (goal scheduler / ``create_task`` / auto-route / reassign — all funnel
    through that single helper). That helper now mints a server-side
    ``request_id``, stamps it onto the injected message metadata (the same
    key the live user-send path uses), and writes one row here linking the
    minted ``request_id`` to the Task it woke.

    When the assignee agent threads that ``request_id`` back onto its
    ``handler_finished`` LifecycleFrame and the turn ends in a terminal
    non-ok outcome (``rejected`` / ``timeout`` / ``failed``), the WS handler
    looks the turn up here: a hit means an assignment turn failed, so the
    Task is returned to ``todo`` and re-dispatched once. A *miss* means a
    live (user-send / peer-handoff) turn — those never write a row here, so
    the bridge leaves them completely untouched (the core scope invariant).

    ``redispatch_count`` is carried forward across the re-dispatch chain:
    each re-wake mints a *fresh* ``request_id`` and writes a *new* row with
    the incremented count, so the flip-loop is bounded by
    ``_MAX_TASK_REDISPATCH`` (=1) even though every wake has its own
    correlation id.
    """

    __tablename__ = "agent_turn_tasks"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    """The minted turn correlation id. PK — the only access pattern is a
    point lookup by ``request_id`` from the lifecycle receive path, so no
    additional index is needed."""

    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The Task this turn was woken to work on. CASCADE so deleting the
    Task tears down its turn-correlation rows (matches the rooms→tasks /
    task_blockers cascades)."""

    redispatch_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    """How many automatic re-dispatches have happened *before* this turn.
    0 for the original assignment; carried + incremented on each re-wake so
    ``_MAX_TASK_REDISPATCH`` bounds the chain."""

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


class Goal(Base):
    """A repeating responsibility owned by an agent (#302).

    Goals carry the *definition* of a recurring duty — when to run,
    what spec to inject, which room to report into, how loud the
    output should be. Each trigger fire produces a ``Task`` row
    (subject to the ``materialize`` policy) which carries the actual
    execution metadata. Goal vs Task split mirrors "schedule" vs
    "instance" — see plan-302 §3.2 D11/D12 for the rationale behind
    not introducing a parallel ``goal_runs`` table.
    """

    __tablename__ = "agent_goals"
    __table_args__ = (
        # Scheduler hot path — "give me every active goal whose
        # next_run_at has elapsed". The composite serves the
        # ``WHERE status='active' AND next_run_at <= now()`` scan.
        Index("ix_agent_goals_status_next_run", "status", "next_run_at"),
        # Owner / room views ("which goals does this agent have?",
        # "what's reporting into this room?").
        Index("ix_agent_goals_assignee", "assignee_agent_id"),
        Index("ix_agent_goals_report_room", "report_room_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    assignee_agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    """The agent that owns and executes the responsibility. NOT NULL —
    a goal without an owner has no one to run it."""

    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    """The user who registered the goal. Drives ownership-based
    permissions for pause / edit / delete (admins also pass)."""

    report_room_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("rooms.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    """Room to which the goal posts results. NULL = silent goal (the
    AgentSettingsDialog is the only surface). Room deletion downgrades
    to NULL rather than cascading the goal away."""

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    spec: Mapped[str] = mapped_column(Text, nullable=False)
    """Markdown — the actual instructions injected into the agent at
    each trigger. Edits take effect on the *next* run; in-flight runs
    keep their snapshot in ``Task.spec``."""

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    """active | paused | completed | failed | abandoned. The scheduler
    only fires for ``active``; ``paused`` keeps the row but skips
    triggers; ``completed`` / ``failed`` / ``abandoned`` are terminal."""

    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    """cron | interval | manual."""

    trigger_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    """jsonb. Shape varies by trigger_type:
       - cron     : {"cron": "0 9 * * *"}
       - interval : {"interval_seconds": 600}
       - manual   : {} (no automatic trigger)
    """

    materialize: Mapped[str] = mapped_column(
        String(32), nullable=False, default="interesting_only"
    )
    """full | interesting_only. Default conservative — quiet goals
    don't clutter Tasks UI. ``digest`` reserved for Phase 2 (requires
    auxiliary log table + summarisation cron)."""

    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    """Reset to 0 on every success. When this hits the policy
    threshold (default 3) the scheduler flips ``status='paused'``
    and posts a heads-up to ``report_room_id``."""

    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    """Issue #449 (Wave 1b) — the instant the scheduler last won the
    atomic CAS claim for this goal (``UPDATE ... WHERE next_run_at <=
    now`` matched a row). Observability / multi-replica diagnostics
    only — the firing contract is enforced by the CAS guard +
    ``Task.idempotency_key`` UNIQUE, not by this column."""
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )


# ── LLM Gateway (#197) ─────────────────────────────────────────────────
#
# A LiteLLM subprocess supervised by anygarden-server routes every agent
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
    # config writer emits ``api_key: os.environ/ANYGARDEN_LITELLM_<ref>`` and
    # the supervisor injects ``ANYGARDEN_LITELLM_<ref>=<decrypted>`` at spawn.
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
    operator-managed ``ANYGARDEN_MCP_SECRETS_KEY`` keeps KMS surface a
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
    # #461 (Wave 2d) — per-request USD cost, nullable. Gateway-routed
    # callers (openhands via the reverse proxy) leave this NULL — the
    # proxy has no provider-cost signal. CLI engines that self-report a
    # cost populate it: claude-code stamps its SDK's ``total_cost_usd``
    # (an *estimate*, not a provider invoice); codex / gemini report no
    # cost and stay NULL. Admin usage aggregation sums it nullable-safe.
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    # Populated only on non-2xx. Short message from upstream or proxy
    # for debug views; never user-facing.
    error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, default=None)


# ── Token budgets (#453, reliability Wave 1d) ──────────────────────────
#
# A policy table on top of the measured ``LLMGatewayUsage`` stream. The
# reverse proxy sums observed tokens over a rolling/calendar window per
# scope and, when an *active* policy with ``hard_stop_enabled`` is over
# its ceiling, refuses the call with 429 at the gateway chokepoint
# (``budgets/ledger.py``). ``hard_stop_enabled`` defaults to False so
# merging this feature is a no-op: with zero active hard-stop policies
# the gate never fires and runtime behaviour is unchanged until an admin
# deliberately creates and enables a policy. Active-stop / incidents /
# pause_reason / USD cost are Wave 2 (out of scope here).


class TokenBudgetPolicy(Base):
    """One token-budget ceiling for a scope (global / agent / room).

    ``scope_type`` selects what ``scope_id`` references:

    - ``global`` — ``scope_id`` is NULL; the ceiling applies to the sum
      of *all* gateway usage in the window.
    - ``agent`` — ``scope_id`` is an ``agents.id``; ceiling applies to
      that agent's usage.
    - ``room`` — ``scope_id`` is a ``rooms.id``; ceiling applies to that
      room's usage (best-effort at the proxy — room correlation is only
      available when tracing resolves a single in-flight request).

    Plain ``String(36)`` for ``scope_id`` with no FK: a deleted agent or
    room should not silently drop the operator's policy, and the ledger
    treats an orphaned ``scope_id`` as simply matching no usage rows.
    """

    __tablename__ = "token_budget_policies"
    __table_args__ = (
        Index(
            "ix_token_budget_policies_scope",
            "scope_type",
            "scope_id",
            "is_active",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # "global" | "agent" | "room".
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # NULL for global; agents.id / rooms.id otherwise. No FK on purpose
    # (see class docstring).
    scope_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, default=None
    )
    # Token ceiling for the window (prompt + completion tokens summed).
    token_ceiling: Mapped[int] = mapped_column(Integer, nullable=False)
    # Soft-warn threshold as a percent of the ceiling. Informational for
    # now (Wave 2 surfaces warnings); the hard-stop gate uses the
    # ceiling itself.
    warn_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    # "rolling_24h" (now - 24h) | "calendar_day_utc" (midnight UTC).
    window_kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="rolling_24h"
    )
    # The kill switch. Defaults False so a freshly-created policy is a
    # no-op until the operator deliberately enables enforcement — the
    # invariant that makes merging this PR behaviour-neutral.
    hard_stop_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Soft delete / disable without losing the configured ceiling.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )


# ── Token-budget incidents (#455, reliability Wave 2a) ─────────────────
#
# Wave 1d refuses the *next* LLM call (429) once a hard-stop policy is
# over ceiling, but a runaway agent just retries and spins forever
# receiving 429s. Wave 2a evaluates the budget *after* each successful
# usage row and records an ``TokenBudgetIncident`` for the breach, and —
# for AGENT-scope hard breaches only — actively stops the offending
# agent (``request_stop`` → desired_state=stopped → machine kills the
# subprocess). ROOM / GLOBAL breaches record an incident only: never
# auto-stop, because killing a whole room or fleet over one shared cap
# would be collateral damage on innocent work — those are surfaced to an
# operator to decide.
#
# Default-OFF is inherited from Wave 1d: ``evaluate_cost_event`` loads
# the same active ``hard_stop_enabled`` policies, of which there are
# none on a fresh DB, so no incident is ever created and no agent is
# ever stopped until an admin deliberately enables a policy.


class TokenBudgetIncident(Base):
    """One recorded budget breach for a (policy, window, threshold).

    Written by ``anygarden.budgets.ledger.evaluate_cost_event`` after a
    successful usage row pushes a scope's observed-token SUM over a
    threshold:

    - ``threshold_type == 'soft'`` — observed reached
      ``ceiling * warn_percent / 100`` but is still under the ceiling.
      Informational only (never stops anything).
    - ``threshold_type == 'hard'`` — observed reached the ceiling. For an
      AGENT-scope policy this is also the row that accompanies the
      active stop; for ROOM / GLOBAL it is incident-only.

    Deduplication: at most one ``status == 'open'`` row per
    (``policy_id``, ``window_start``, ``threshold_type``). The same
    window is crossed by dozens of calls, so without the dedup gate the
    table would explode with one row per over-ceiling request. Admin
    resume marks open incidents ``resolved`` and stamps ``resolved_at``.

    ``scope_type`` / ``scope_id`` are denormalized from the policy so the
    admin surface can group incidents by scope without a join (and they
    survive the policy being deleted). No FK on ``policy_id`` for the
    same reason the policy carries no FK on its ``scope_id``: a deleted
    policy must not silently erase the audit trail of breaches it caused.
    """

    __tablename__ = "token_budget_incidents"
    __table_args__ = (
        Index(
            "ix_token_budget_incidents_policy_status",
            "policy_id",
            "status",
        ),
        Index(
            "ix_token_budget_incidents_scope",
            "scope_type",
            "scope_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # The policy whose ceiling/warn threshold was crossed. Plain string,
    # no FK (see class docstring) — a deleted policy keeps its history.
    policy_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Denormalized from the policy: "global" | "agent" | "room".
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # NULL for global; agents.id / rooms.id otherwise.
    scope_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, default=None
    )
    # Inclusive lower bound of the budget window the breach was observed
    # in. Together with policy_id + threshold_type it's the dedup key.
    window_start: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    # "soft" (warn threshold) | "hard" (ceiling).
    threshold_type: Mapped[str] = mapped_column(String(8), nullable=False)
    # "open" (active breach) | "resolved" (admin acknowledged / resumed).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open"
    )
    # The observed-token SUM at the moment the incident was recorded.
    observed_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )


class VersionCheck(Base):
    """Cached result of the last PyPI update check per package (#546).

    One row per package (``anygarden``, ``anygarden-machine``). The admin
    ``check-updates`` endpoint upserts here; the ``updates`` endpoint and
    the UI badge read from it without any outbound call. Persisting to the
    DB (rather than in-memory) keeps the last-known state across restarts
    and lets a future background poller fill the same cache unchanged.
    """
    __tablename__ = "version_checks"

    # Package name is the natural primary key — one cache row per package.
    package: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Latest version seen on PyPI; NULL until a successful check.
    latest_version: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, default=None
    )
    # When the last check ran (success or failure); NULL if never checked.
    checked_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, nullable=True, default=None
    )
    # Failure reason (e.g. "unreachable") when the last check could not
    # resolve a version; NULL on success.
    error: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, default=None
    )
