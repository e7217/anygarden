"""Pydantic v2 frame models for the WebSocket protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel


# ── Incoming (client → server) ────────────────────────────────────────


class SendFrame(BaseModel):
    type: Literal["send"] = "send"
    content: str
    metadata: Optional[dict[str, Any]] = None


class TypingFrame(BaseModel):
    type: Literal["typing"] = "typing"
    is_typing: bool = True


class CreateRoomFrame(BaseModel):
    type: Literal["create_room"] = "create_room"
    project_id: str
    name: str
    is_dm: bool = False


class JoinRoomFrame(BaseModel):
    type: Literal["join_room"] = "join_room"
    room_id: str


class LifecycleFrame(BaseModel):
    """Agent-emitted handler/engine lifecycle event (cluster mirror).

    MUST stay field-compatible with
    ``doorae_agent.protocol.frames.LifecycleFrame``. The cluster
    persists these verbatim into ``ActivityLog`` under the
    propagated ``request_id``.

    Design reference: docs/plans/2026-04-20-agent-observability-design.md
    §2 "Wire protocol".
    """
    type: Literal["lifecycle"] = "lifecycle"
    request_id: str
    room_id: str
    event: Literal[
        "handler_started",
        "handler_finished",
        "engine_call_started",
        "engine_call_finished",
    ]
    outcome: Optional[
        Literal["ok", "failed", "timeout", "cancelled", "rejected"]
    ] = None
    duration_ms: Optional[int] = None
    engine: Optional[str] = None
    error: Optional[str] = None


IncomingFrame = (
    SendFrame | TypingFrame | CreateRoomFrame | JoinRoomFrame | LifecycleFrame
)


def parse_incoming(data: dict[str, Any]) -> IncomingFrame:
    """Dispatch raw JSON to the correct frame model."""
    frame_type = data.get("type")
    match frame_type:
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
    """

    id: str
    display_name: str
    kind: Literal["user", "agent", "guest"]
    agent_id: Optional[str] = None


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
    # Orchestrator agents inject this list into their LLM system
    # prompt so the model can call ``handoff_to`` with a valid
    # ``participant_id`` (UUID) instead of guessing a display name.
    # Defaults to an empty list so pre-#221 clients see no change in
    # semantics when the server is rolled forward first.
    participants: list[ParticipantBrief] = []
    # Issue #237 — ephemeral toggle: when True the agent's system
    # prompt gets a directive to skip writing to memory/notes.md.
    # Trust-model signal (see plan §3.2 decision 3). Default False
    # keeps existing welcome frames unchanged for non-ephemeral rooms.
    ephemeral: bool = False
    # Issue #237 — the per-agent long-term memory snapshot (markdown).
    # Populated from ``agents.memory_md`` when the WS session belongs to
    # an agent; None for user/guest connections or agents with empty
    # memory. The SDK stamps this into the engine adapter's system
    # prompt via ``compose_memory_block``.
    memory_md: Optional[str] = None


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
    event: Literal["created", "updated", "deleted", "assigned", "reassigned"]
    task: dict[str, Any]


OutgoingFrame = (
    MessageOut
    | RoomCreatedOut
    | JoinRoomOut
    | RoomDeletedOut
    | RoomMembershipChangedOut
    | RoomPinOrderChangedOut
    | TypingOut
    | PresenceUpdateOut
    | WelcomeOut
    | RoomSettingsChangedOut
    | TaskUpdateOut
    | ErrorOut
)
