"""Pydantic v2 frame models for the WebSocket protocol.

This file MUST stay in sync with the server's ``anygarden/ws/protocol.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ── Incoming (client -> server) ──────────────────────────────────────


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
            # #720 — the agent's policy declined a delivered turn (SKIP or
            # INGEST_ONLY). Closes the durable turn without a reply, retry
            # or task redispatch so it cannot block later deliveries.
            "skipped",
        ]
    ] = None
    duration_ms: Optional[int] = Field(default=None, strict=True, ge=0, le=(1 << 63) - 1)
    engine: Optional[str] = None
    error: Optional[str] = None
    # Actual native receipt, authenticated against this exact leased attempt.
    # These fields carry no PID, native handle, raw provider error or secret.
    local_execution_id: Optional[str] = Field(default=None, min_length=36, max_length=36)
    native_outcome: Optional[Literal["succeeded", "failed", "cancelled", "unknown"]] = None
    native_process_state: Optional[Literal["not_started", "running", "finished", "stopped", "unknown"]] = None
    native_reason_code: Optional[Literal[
        "MODEL_CONFIGURATION_INVALID", "AUTHENTICATION_FAILED", "MODEL_TRANSIENT_FAILURE",
        "MODEL_EXECUTION_FAILED", "PROCESS_OUTCOME_UNKNOWN",
    ]] = None
    native_transient: Optional[bool] = Field(default=None, strict=True)
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


# ── Outgoing (server -> client) ──────────────────────────────────────


class MessageOut(BaseModel):
    type: Literal["message"] = "message"
    id: str = ""
    room_id: str
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


class TypingOut(BaseModel):
    type: Literal["typing"] = "typing"
    room_id: str
    participant_id: str
    is_typing: bool
    stage: Optional[Literal["preparing", "using_tool", "writing"]] = None


class WelcomeOut(BaseModel):
    type: Literal["welcome"] = "welcome"
    participant_id: str
    pending_rooms: list[str] = []
    room_memory: dict | None = None


class ErrorOut(BaseModel):
    type: Literal["error"] = "error"
    detail: str


OutgoingFrame = (
    MessageOut | RoomCreatedOut | JoinRoomOut | TypingOut | WelcomeOut | ErrorOut
    | TurnStartPermitOut | TurnStopOut
)
