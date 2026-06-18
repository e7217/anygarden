"""Pydantic v2 frame models for the WebSocket protocol.

This file MUST stay in sync with the server's ``anygarden/ws/protocol.py``.
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
        Literal[
            "ok",
            "failed",
            "timeout",
            "cancelled",
            "rejected",
            # #457 Wave 2b — bounded per-room queue + transient retry.
            # ``queued``: a follow-up turn was deferred (lock held, under
            # cap) and will run FIFO after the in-flight turn drains —
            # the durable replacement for ``rejected``-on-overflow.
            # ``retrying``/``retry_exhausted``: an opt-in transient retry
            # (default OFF) re-ran an empty failed/timeout turn, then
            # eventually gave up. None of these change the ``event``
            # Literal — they are terminal results of ``handler_finished``.
            "queued",
            "retrying",
            "retry_exhausted",
        ]
    ] = None
    duration_ms: Optional[int] = None
    engine: Optional[str] = None
    error: Optional[str] = None
    # #433 — gateway-free LLM turn I/O. On ``engine_call_finished`` the
    # supervisor may carry the augmented input the adapter handed the
    # engine (``prompt``) and the engine's reply (``completion``) so the
    # cluster stamps them onto the ``agent.engine_call`` span without
    # routing through the LLM gateway. Trace-only — never persisted to
    # ActivityLog (the cluster's ``_lifecycle_details`` selects fields).
    # Privacy note: these travel the same internal agent↔cluster WS the
    # reply already uses; the ``otel_llm_capture_content`` toggle is a
    # *cluster-side span gate* (it suppresses the attribute, not the wire
    # field). ``completion`` duplicates the posted reply; ``prompt`` is
    # the one genuinely new payload on the wire.
    prompt: Optional[str] = None
    completion: Optional[str] = None
    # #461 (Wave 2d) — gateway-free LLM usage telemetry. CLI engines
    # (claude-code / codex / gemini) don't route through the LLM gateway,
    # so their token usage never reached the central ``LLMGatewayUsage``
    # table. On ``engine_call_finished`` an adapter that can read its
    # engine SDK's usage carries it here; the cluster persists one usage
    # row. ``model`` is the resolved model name; ``input_tokens`` /
    # ``output_tokens`` are prompt / completion counts; ``cost_usd`` is
    # the SDK-self-reported turn cost (claude-code's ``total_cost_usd`` —
    # an estimate, not a provider invoice). Unlike prompt/completion TEXT
    # (which stays behind the cluster-side ``capture_content`` span gate),
    # token COUNTS are non-sensitive and always carried when reported.
    # All ``None`` for a bare-str engine return or an adapter that can't
    # surface usage (openhands leaves them None — it is already counted
    # via the gateway reverse-proxy — so no double-counting).
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None


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
