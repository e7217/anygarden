"""Pydantic v2 frame models for the WebSocket protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ── Incoming (client → server) ────────────────────────────────────────


class ExecutionControlResultFrame(BaseModel):
    """Only authenticated current agent control responses enter this frame."""
    model_config = {"extra": "forbid"}
    type: Literal["execution_control_result"] = "execution_control_result"
    request_id: str = Field(min_length=1, max_length=128)
    action: Literal["prepare", "describe", "start", "reconcile", "cancel", "revoke"]
    generation: int = Field(ge=0, strict=True)
    result: dict[str, Any] | None = None
    error_code: Literal[
        "EXECUTION_UNAVAILABLE", "UNSUPPORTED_PERMISSION", "AUTH_MISSING", "INVALID_REQUEST",
        "EXECUTION_CONFLICT", "EXECUTION_UNKNOWN", "GENERATION_CHANGED", "CONFIGURATION_CHANGED",
        "POLICY_DENIED", "EXECUTION_QUEUE_FULL", "CONTROL_DISCONNECTED", "LOCAL_STORAGE_UNSAFE",
        "WRONG_CONTROL_ROOM",
    ] | None = None


class ExecutionControlOut(BaseModel):
    model_config = {"extra": "forbid"}
    type: Literal["execution_control"] = "execution_control"
    request_id: str = Field(min_length=1, max_length=128)
    agent_id: str
    generation: int = Field(ge=0, strict=True)
    action: Literal["prepare", "describe", "start", "reconcile", "cancel", "revoke"]
    payload: dict[str, Any]


class TurnStartFrame(BaseModel):
    """Exact delivered attempt asks permission immediately before native spawn."""
    model_config = {"extra": "forbid"}
    type: Literal["turn_start"] = "turn_start"
    request_id: str = Field(min_length=1, max_length=128)
    attempt: int = Field(ge=1, strict=True)
    generation: int = Field(ge=0, strict=True)
    lease: str = Field(min_length=1, max_length=128, repr=False)
    local_execution_id: str = Field(min_length=36, max_length=36)
    execution_id: str | None = Field(default=None, min_length=36, max_length=36)
    input_revision: int | None = Field(default=None, ge=1, strict=True)


class TurnStartPermitOut(BaseModel):
    model_config = {"extra": "forbid"}
    type: Literal["turn_start_permit"] = "turn_start_permit"
    room_id: str
    request_id: str = Field(min_length=1, max_length=128)
    attempt: int = Field(ge=1, strict=True)
    generation: int = Field(ge=0, strict=True)
    local_execution_id: str = Field(min_length=36, max_length=36)
    execution_id: str | None = Field(default=None, min_length=36, max_length=36)
    input_revision: int | None = Field(default=None, ge=1, strict=True)
    allowed: bool
    code: str | None = Field(default=None, max_length=128)
    input_snapshot: dict[str, Any] | None = None


class TurnStopOut(BaseModel):
    model_config = {"extra": "forbid"}
    type: Literal["turn_stop"] = "turn_stop"
    stop_id: str = Field(min_length=36, max_length=36)
    agent_id: str
    room_id: str
    request_id: str = Field(min_length=1, max_length=128)
    attempt: int = Field(ge=1, strict=True)
    generation: int = Field(ge=0, strict=True)
    execution_id: str = Field(min_length=36, max_length=36)
    input_revision: int = Field(ge=1, strict=True)
    local_execution_id: str | None = Field(default=None, min_length=36, max_length=36)
    reason: str = Field(min_length=1, max_length=128)


class TurnStopResultFrame(BaseModel):
    """Process receipt only; contains neither leases nor native session handles."""
    model_config = {"extra": "forbid"}
    type: Literal["turn_stop_result"] = "turn_stop_result"
    stop_id: str = Field(min_length=36, max_length=36)
    request_id: str = Field(min_length=1, max_length=128)
    attempt: int = Field(ge=1, strict=True)
    generation: int = Field(ge=0, strict=True)
    execution_id: str = Field(min_length=36, max_length=36)
    input_revision: int = Field(ge=1, strict=True)
    local_execution_id: str | None = Field(default=None, min_length=36, max_length=36)
    status: Literal["confirmed", "not_started", "already_finished", "unknown"]
    process_state: Literal["stopped", "not_started", "finished", "unknown"] = "unknown"
    outcome: Literal["succeeded", "failed", "cancelled", "unknown"] | None = None
    code: str | None = Field(default=None, max_length=128)


class SendFrame(BaseModel):
    type: Literal["send"] = "send"
    content: str
    metadata: Optional[dict[str, Any]] = None
    # Clients provide only the top-level root id. The server derives both
    # persisted relationship columns and rejects nested/cross-room replies.
    thread_root_id: Optional[str] = None


class TypingFrame(BaseModel):
    type: Literal["typing"] = "typing"
    is_typing: bool = True
    stage: Optional[Literal["preparing", "using_tool", "writing"]] = None


class CreateRoomFrame(BaseModel):
    type: Literal["create_room"] = "create_room"
    project_id: str
    name: str
    is_dm: bool = False


class JoinRoomFrame(BaseModel):
    type: Literal["join_room"] = "join_room"
    room_id: str


class _UsageCounters(BaseModel):
    model_config = {"extra": "forbid"}
    input_tokens: int | None = Field(default=None, strict=True, ge=0, le=(1 << 63) - 1)
    output_tokens: int | None = Field(default=None, strict=True, ge=0, le=(1 << 63) - 1)
    cached_input_tokens: int | None = Field(default=None, strict=True, ge=0, le=(1 << 63) - 1)


class _UsageMetadata(BaseModel):
    model_config = {"extra": "forbid"}
    source: Literal["codex-session-cumulative-v1"]
    status: Literal[
        "fresh_measured", "measured_delta", "baseline_missing", "baseline_invalidated",
        "counter_rollback", "session_changed", "session_unobserved",
        "terminal_usage_missing", "provider_usage_invalid", "process_outcome_unknown",
        "not_started",
    ]
    baseline_status: Literal["fresh", "valid", "absent", "invalidated"]
    provider_cumulative: _UsageCounters | None = None
    baseline: _UsageCounters | None = None
    requested_session_sha256: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    observed_session_sha256: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    baseline_execution_sha256: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

class LifecycleFrame(BaseModel):
    """Agent-emitted handler/engine lifecycle event (cluster mirror).

    MUST stay field-compatible with
    ``anygarden_agent.protocol.frames.LifecycleFrame``. The cluster
    persists these verbatim into ``ActivityLog`` under the
    propagated ``request_id``.

    Design reference: docs/plans/2026-04-20-agent-observability-design.md
    §2 "Wire protocol".
    """
    type: Literal["lifecycle"] = "lifecycle"
    request_id: str
    room_id: str
    turn_attempt: Optional[int] = None
    turn_generation: Optional[int] = None
    turn_lease: Optional[str] = None
    event: Literal[
        "handler_started",
        "handler_finished",
        "engine_call_started",
        "engine_call_finished",
    ]
    outcome: Optional[
        Literal[
            "ok",
            "failed",
            "timeout",
            "cancelled",
            "rejected",
            # #457 Wave 2b — MUST mirror the agent frame Literal exactly
            # (test_protocol_compat enforces parity). ``queued`` defers a
            # follow-up turn (durable replacement for ``rejected``);
            # ``retrying``/``retry_exhausted`` are the opt-in transient
            # retry (default OFF) terminal outcomes. ``event`` unchanged.
            "queued",
            "retrying",
            "retry_exhausted",
            # #720 — agent policy declined the turn; closes it terminally.
            "skipped",
        ]
    ] = None
    duration_ms: Optional[int] = Field(default=None, strict=True, ge=0, le=(1 << 63) - 1)
    engine: Optional[str] = None
    error: Optional[str] = None
    # Mirror the agent's exact native receipt; authorization is server-side.
    local_execution_id: Optional[str] = Field(default=None, min_length=36, max_length=36)
    native_outcome: Optional[Literal["succeeded", "failed", "cancelled", "unknown"]] = None
    native_process_state: Optional[Literal["not_started", "running", "finished", "stopped", "unknown"]] = None
    native_reason_code: Optional[Literal[
        "MODEL_CONFIGURATION_INVALID", "AUTHENTICATION_FAILED", "MODEL_TRANSIENT_FAILURE",
        "MODEL_EXECUTION_FAILED", "PROCESS_OUTCOME_UNKNOWN",
    ]] = None
    native_transient: Optional[bool] = Field(default=None, strict=True)
    # #433 — gateway-free LLM turn I/O captured at the agent's engine
    # adapter and stamped onto the ``agent.engine_call`` span. Consumed
    # only by ``_apply_lifecycle_to_trace``; ``_lifecycle_details`` does
    # not select these, so they never reach ActivityLog.
    prompt: Optional[str] = None
    completion: Optional[str] = None
    # #461 (Wave 2d) — gateway-free LLM usage telemetry. MUST stay
    # field-compatible with the agent frame (test_protocol_compat
    # enforces parity). The WS handler writes one ``UsageLedger``
    # row from these on ``engine_call_finished`` when ``input_tokens`` /
    # ``output_tokens`` is set OR a ``model`` is present; token COUNTS
    # are non-sensitive and persisted, while prompt/completion TEXT stays
    # behind the existing capture_content span gate. All ``None`` for a
    # bare-str engine return or openhands (already counted via the
    # gateway reverse-proxy), so no double-counted row is written.
    model: Optional[str] = None
    input_tokens: Optional[int] = Field(default=None, strict=True, ge=0, le=(1 << 63) - 1)
    output_tokens: Optional[int] = Field(default=None, strict=True, ge=0, le=(1 << 63) - 1)
    cost_usd: Optional[float] = Field(default=None, strict=True, ge=0, allow_inf_nan=False)
    usage_metadata: dict[str, Any] | None = None

    @field_validator("usage_metadata")
    @classmethod
    def _bounded_usage_metadata(cls, value):
        return _UsageMetadata.model_validate(value).model_dump() if value is not None else None


IncomingFrame = (
    SendFrame | TypingFrame | CreateRoomFrame | JoinRoomFrame | LifecycleFrame | ExecutionControlResultFrame
    | TurnStartFrame | TurnStopResultFrame
)


def parse_incoming(data: dict[str, Any]) -> IncomingFrame:
    """Dispatch raw JSON to the correct frame model."""
    frame_type = data.get("type")
    match frame_type:
        case "turn_start":
            return TurnStartFrame.model_validate(data)
        case "turn_stop_result":
            return TurnStopResultFrame.model_validate(data)
        case "execution_control_result":
            return ExecutionControlResultFrame.model_validate(data)
        case "send":
            return SendFrame.model_validate(data)
        case "typing":
            return TypingFrame.model_validate(data)
        case "create_room":
            return CreateRoomFrame.model_validate(data)
        case "join_room":
            return JoinRoomFrame.model_validate(data)
        case "lifecycle":
            return LifecycleFrame.model_validate(data)
        case _:
            raise ValueError(f"Unknown frame type: {frame_type!r}")


# ── Outgoing (server → client) ────────────────────────────────────────


class MessageOut(BaseModel):
    type: Literal["message"] = "message"
    id: str = ""
    room_id: str
    # None if the original sender has been removed from the room (FK SET NULL).
    participant_id: Optional[str] = None
    content: str
    parent_message_id: Optional[str] = None
    root_message_id: Optional[str] = None
    seq: int
    created_at: datetime
    metadata: Optional[dict[str, Any]] = None


class RoomCreatedOut(BaseModel):
    type: Literal["room_created"] = "room_created"
    room_id: str
    name: str


class JoinRoomOut(BaseModel):
    type: Literal["join_room"] = "join_room"
    room_id: str
    participant_id: str


class RoomDeletedOut(BaseModel):
    """A room has been removed.

    Distinct from ``RoomMembershipChangedOut`` which signals
    individual add/remove. Here the room itself ceases to exist, so
    the frontend should:
    - drop the room from its tree (sidebar refresh),
    - if the user is currently viewing the deleted room, navigate
      away to a safe place (project root or fallback).
    """

    type: Literal["room_deleted"] = "room_deleted"
    room_id: str


class RoomMembershipChangedOut(BaseModel):
    """Notify a user that their membership in a room has changed.

    Sent over an existing user WS connection so the frontend can
    refresh its room list (sidebar) without polling. Distinct from
    ``JoinRoomOut`` which the agent SDK uses to trigger an automatic
    WS connection to the new room.
    """

    type: Literal["room_membership_changed"] = "room_membership_changed"
    action: Literal["added", "removed"]
    room_id: str
    user_id: str


class RoomPinOrderChangedOut(BaseModel):
    """Notify the caller's other sessions that pin state changed (#47).

    Emitted only to the sessions of ``user_id`` — pinning is per-user
    so no other listeners care. ``pinned_room_ids`` is the full new
    order of the user's pinned sidebar section, letting the client
    replace local state without a follow-up GET.
    """

    type: Literal["room_pin_order_changed"] = "room_pin_order_changed"
    user_id: str
    pinned_room_ids: list[str]


class TypingOut(BaseModel):
    type: Literal["typing"] = "typing"
    room_id: str
    participant_id: str
    is_typing: bool
    stage: Optional[
        Literal["preparing", "using_tool", "writing", "waiting_peers"]
    ] = None
    # #762 — ``waiting_peers`` only: the server keeps an ``ask_peer``
    # caller's indicator alive while its peers answer.
    waiting_done: Optional[int] = None
    waiting_total: Optional[int] = None
    waiting_names: Optional[list[str]] = None


class PresenceUpdateOut(BaseModel):
    """A participant's WS subscription state changed (#54).

    Emitted from ``ConnectionManager.subscribe``/``unsubscribe`` via
    ``PresenceService.publish``. Frontend consumers merge this into
    ``useParticipantPresence`` state so dots and "last seen" labels
    refresh in near real time without polling.
    """

    type: Literal["presence_update"] = "presence_update"
    room_id: str
    participant_id: str
    online: bool
    last_seen_at: Optional[datetime] = None


class ParticipantBrief(BaseModel):
    """Lightweight participant identity for WS payloads (#221).

    Slimmer than the REST ``ParticipantOut`` — only the fields an
    agent needs to populate a handoff roster or render a presence
    marker. ``display_name`` is the resolved human-readable label
    (user email/display_name or agent name) so consumers don't have
    to cross-reference a separate lookup. ``agent_id`` is set only
    for agent participants; user/guest participants leave it ``None``.

    ``description`` (#271) is the agent's public-facing self-introduction
    sourced from ``Agent.description``. Always ``None`` for user/guest
    participants; capped at 200 chars by the REST layer that writes it.
    Surfaced both to the LLM roster (so peers' models can recognize this
    agent semantically) and the frontend mention/participant popovers.
    """

    id: str
    display_name: str
    kind: Literal["user", "agent", "guest"]
    agent_id: Optional[str] = None
    description: Optional[str] = None


class WelcomeOut(BaseModel):
    type: Literal["welcome"] = "welcome"
    participant_id: str
    pending_rooms: list[str] = []
    # Issue #61 — ``agent_id`` is present only on agent connections.
    # The agent SDK uses it to gate ``room_query`` forwarding to the
    # representative agent: the server broadcasts ``room_query``
    # metadata (incl. ``representative_agent_id``) to the whole room
    # and each agent checks ``agent_id == representative_agent_id``
    # before forwarding. Without this gate every agent in the source
    # room re-forwards the ``[ROOM_QUERY]`` message. ``None`` for user
    # and guest connections.
    agent_id: Optional[str] = None
    # Issue #148 Part 3 — agent-side ambient opt-out, read from
    # ``agents.context_window_opt_out`` at welcome time. The SDK
    # caches this and consults it in ``decide_policy``: when the
    # server marks a message ``ingest_only`` AND this flag is True,
    # the agent returns ``SKIP`` instead of ``INGEST_ONLY``. Default
    # False so user/guest welcome frames stay unchanged.
    context_window_opt_out: bool = False
    # Issue #159 Phase A — room-scoped speaker strategy. Agents cache
    # these from the welcome and dispatch in ``decide_policy``. Defaults
    # preserve the legacy behaviour for rooms that haven't opted in.
    # - ``speaker_strategy``: 'mentioned_only' (default) | 'round_robin'
    #   | 'orchestrator'. Phase B/C wire the non-default branches.
    # - ``orchestrator_agent_id``: agent that issues handoffs under the
    #   ``orchestrator`` strategy. Distinct from ``representative_agent_id``
    #   (cross-room query role) — same Agent may hold both.
    # - ``next_speaker_participant_id``: orchestrator's latest handoff
    #   target; read by the agent to decide whether to RESPOND.
    speaker_strategy: str = "mentioned_only"
    orchestrator_agent_id: Optional[str] = None
    next_speaker_participant_id: Optional[str] = None
    # Issue #221 — room participants roster, stamped at welcome time.
    # Agents render this list into their LLM prompt so the model can
    # pass a valid ``participant_id`` (UUID) to the ``ask_peer`` tool
    # (#737) instead of guessing a display name.
    # Defaults to an empty list so pre-#221 clients see no change in
    # semantics when the server is rolled forward first.
    participants: list[ParticipantBrief] = []
    # Issue #237 — ephemeral toggle: when True the agent's system
    # prompt gets a directive to skip writing to memory/notes.md.
    # Trust-model signal (see plan §3.2 decision 3). Default False
    # keeps existing welcome frames unchanged for non-ephemeral rooms.
    ephemeral: bool = False
    # The room's current head ``seq`` at welcome time. An agent with no
    # persisted cursor for this room seeds one from it, so its *next*
    # reconnect can ask ``since_seq`` for the gap instead of starting at
    # 0 (which replays nothing and loses everything sent while it was
    # down). 0 on an empty room; pre-existing clients ignore the field.
    last_seq: int = 0
    # Compatibility field is always empty. Unattributed global memory is an
    # archive, not runtime context; new clients select only room_memory.
    memory_md: Optional[str] = None
    room_memory: dict[str, Any] | None = None


class RoomMemoryChangedOut(BaseModel):
    """Only the owning agent's participant in this room receives this body."""

    type: Literal["room_memory_changed"] = "room_memory_changed"
    agent_id: str
    room_id: str
    room_memory: dict[str, Any]


class RoomSettingsChangedOut(BaseModel):
    """Notify a room's subscribers that admin-editable settings changed (#221).

    Emitted by ``PATCH /api/v1/rooms/{room_id}`` when any of the
    cached-at-welcome fields is updated. Fields left ``None`` mean
    "not part of this change" so a rename-only PATCH doesn't
    accidentally reset other settings in client caches. Agents read
    this to refresh their per-room ``speaker_strategy`` /
    ``orchestrator_agent_id`` / ``context_window_opt_out`` without
    requiring a reconnection — before #221 those values were only
    delivered in the initial ``welcome`` frame.
    """

    type: Literal["room_settings_changed"] = "room_settings_changed"
    room_id: str
    speaker_strategy: Optional[str] = None
    orchestrator_agent_id: Optional[str] = None
    context_window_enabled: Optional[bool] = None
    # #237 — ephemeral mode toggle. None means "not part of this PATCH"
    # so other setting fields aren't implicitly reset on receivers.
    ephemeral: Optional[bool] = None
    # #644 — full participant snapshot, emitted whenever a membership
    # change or an ``Agent.name`` / ``Agent.description`` edit changes
    # what a roster line renders. Pre-#644 the roster shipped only in
    # the welcome frame, so a connected agent's cache went stale the
    # moment anyone joined, left, or rewrote their introduction.
    #
    # A *snapshot* rather than a delta: the receiver replaces its cache
    # wholesale, which is idempotent and needs no merge logic on the
    # SDK side. ``None`` keeps the established "not part of this
    # change" semantics so a settings-only PATCH doesn't wipe a
    # receiver's roster.
    participants: Optional[list[ParticipantBrief]] = None


class ErrorOut(BaseModel):
    type: Literal["error"] = "error"
    detail: str


class TaskUpdateOut(BaseModel):
    """Per-user push for the agent-profile 2차 view (#266 Step 6).

    Emitted whenever a task is created, updated, deleted, or
    (re)assigned. Goes to the room channel so the 1차 view can update
    incrementally, AND to every admin user's WS sessions via
    ``ConnectionManager.push_to_users`` so the 2차 view stays live
    even when the admin isn't subscribed to the originating room.

    ``task`` is intentionally typed as a free-form ``dict[str, Any]`` so
    callers can shape the payload to match the REST schema they want to
    surface (room TaskOut vs agent AgentTaskOut). The frontend treats
    this as opaque metadata it merges into local state.
    """

    type: Literal["task.updated"] = "task.updated"
    event: Literal[
        "created", "updated", "deleted", "assigned", "reassigned", "claimed"
    ]
    task: dict[str, Any]


class RoomArtifactAddedOut(BaseModel):
    """A new agent-produced artifact landed in the room (#290 Phase B).

    Emitted to every subscriber so the right-hand artifact panel can
    refresh without polling. ``artifact`` mirrors the REST list
    payload — frontend treats it as opaque metadata it merges into
    local state, identical to the TaskUpdate flow above.
    """

    type: Literal["room_artifact.added"] = "room_artifact.added"
    artifact: dict[str, Any]


class RoomArtifactRemovedOut(BaseModel):
    """An artifact was deleted from the room (#290 Phase B)."""

    type: Literal["room_artifact.removed"] = "room_artifact.removed"
    room_id: str
    artifact_id: str


OutgoingFrame = (
    ExecutionControlOut
    | TurnStartPermitOut
    | TurnStopOut
    | MessageOut
    | RoomCreatedOut
    | JoinRoomOut
    | RoomDeletedOut
    | RoomMembershipChangedOut
    | RoomPinOrderChangedOut
    | TypingOut
    | PresenceUpdateOut
    | WelcomeOut
    | RoomMemoryChangedOut
    | RoomSettingsChangedOut
    | TaskUpdateOut
    | RoomArtifactAddedOut
    | RoomArtifactRemovedOut
    | ErrorOut
)
