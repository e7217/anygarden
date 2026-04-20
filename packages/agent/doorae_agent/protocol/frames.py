"""Pydantic v2 frame models for the WebSocket protocol.

This file MUST stay in sync with the server's ``doorae/ws/protocol.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel


# ── Incoming (client -> server) ──────────────────────────────────────


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
    """Agent-emitted handler/engine lifecycle event.

    Sent over the per-room WS when the agent-side supervisor enters
    or exits a phase. The cluster persists these verbatim into
    ``ActivityLog`` so a single ``request_id`` can be traced end to
    end: ``message_received`` (cluster) → ``handler_started`` →
    ``engine_call_started`` → ``engine_call_finished`` →
    ``handler_finished`` → ``response_sent`` (cluster).

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


# ── Outgoing (server -> client) ──────────────────────────────────────


class MessageOut(BaseModel):
    type: Literal["message"] = "message"
    room_id: str
    participant_id: str
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
