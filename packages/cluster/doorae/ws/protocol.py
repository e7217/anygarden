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


class TypingOut(BaseModel):
    type: Literal["typing"] = "typing"
    room_id: str
    participant_id: str
    is_typing: bool


class WelcomeOut(BaseModel):
    type: Literal["welcome"] = "welcome"
    participant_id: str
    pending_rooms: list[str] = []


class ErrorOut(BaseModel):
    type: Literal["error"] = "error"
    detail: str


OutgoingFrame = MessageOut | RoomCreatedOut | JoinRoomOut | TypingOut | WelcomeOut | ErrorOut
