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


IncomingFrame = SendFrame | TypingFrame | CreateRoomFrame | JoinRoomFrame


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


class WelcomeOut(BaseModel):
    type: Literal["welcome"] = "welcome"
    participant_id: str
    pending_rooms: list[str] = []


class ErrorOut(BaseModel):
    type: Literal["error"] = "error"
    detail: str


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
    | ErrorOut
)
