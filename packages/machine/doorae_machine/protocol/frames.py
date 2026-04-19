"""Pydantic frame models for Machine <-> Server WebSocket protocol.

This module implements a declarative desired-state protocol replacing the
older imperative spawn_agent/kill_agent commands.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


# ── Server -> Machine frames ──────────────────────────────────────────


class SyncDesiredStateFrame(BaseModel):
    """Server declares the desired state for a single agent.

    The machine reconciles its actual state toward the desired state:
    - desired_state="running" → ensure agent process is running
    - desired_state="stopped" → ensure agent process is not running

    Includes all configuration needed to start/restart the agent.
    """

    type: Literal["sync_desired_state"] = "sync_desired_state"
    agent_id: str
    desired_state: Literal["running", "stopped"]
    generation: int

    # Agent configuration (only relevant when desired_state="running")
    engine: str = ""
    name: str = ""
    profile_yaml: str = ""
    rooms: list[str] = Field(default_factory=list)

    # Per-agent directory manifest
    agents_md: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    engine_secrets: dict[str, str] = Field(default_factory=dict)

    # Per-agent reasoning effort (low/medium/high/etc — engine-dependent)
    reasoning_effort: str | None = None

    # Per-agent engine model (e.g. "gpt-5.4-mini"). None = adapter default.
    model: str | None = None

    # Sub-rooms this agent can delegate to (v2 delegation)
    # Each entry: {"name": "...", "description": "..." or null}
    sub_rooms: list[dict[str, str | None]] = Field(default_factory=list)

    # Issue #73 — which runtime hosts this agent on the machine.
    # ``"python"`` spawns doorae-agent; ``"typescript"`` spawns
    # doorae-agent-ts. Defaults to ``"python"`` so pre-#73 servers
    # stay compatible without re-emitting the field.
    runtime: str = "python"

    # Restart policy
    restart_policy: Literal[
        "stop", "restart_on_same_machine", "restart_anywhere"
    ] = "restart_anywhere"
    max_restarts: int = 3
    restart_window_seconds: int = 300


class SyncBatchFrame(BaseModel):
    """Server syncs desired state for multiple agents in a single frame.

    When ``is_full_snapshot`` is True (the historical and default
    behaviour) the batch represents the complete list of agents the
    server wants placed on this machine. Agents running locally but
    missing from the batch are treated as orphans and stopped.

    When ``is_full_snapshot`` is False, the batch is a targeted
    update: the machine reconciles only the agents listed and does
    **not** kill anything absent. This mode exists for #185 — a
    server-side bug (failed query, empty filter) that sends an empty
    batch would otherwise mass-kill every local agent.

    Default remains True so pre-#185 servers that omit the flag
    retain full-snapshot semantics; mixed-version rollouts are safe
    in either order.
    """

    type: Literal["sync_batch"] = "sync_batch"
    agents: list[SyncDesiredStateFrame] = Field(default_factory=list)
    is_full_snapshot: bool = True


class TokenGrantFrame(BaseModel):
    """Server grants an authentication token to an agent.

    Sent in response to a TokenRequestFrame from the machine.
    """

    type: Literal["token_grant"] = "token_grant"
    agent_id: str
    agent_token: str


class DrainFrame(BaseModel):
    """Server instructs machine to drain (stop accepting new agents)."""

    type: Literal["drain"] = "drain"


class PingFrame(BaseModel):
    """Server ping for keepalive."""

    type: Literal["ping"] = "ping"


class RotateTokenFrame(BaseModel):
    """Server pushes a new machine token after rotation.

    Sent before the server forcibly disconnects the daemon. The daemon
    persists the new token to ``~/.doorae/machine.token`` and uses it
    on the next reconnection.
    """

    type: Literal["rotate_token"] = "rotate_token"
    new_token: str


ServerFrame = Union[
    SyncDesiredStateFrame,
    SyncBatchFrame,
    TokenGrantFrame,
    DrainFrame,
    PingFrame,
    RotateTokenFrame,
]


# ── Machine -> Server frames ─────────────────────────────────────────


class RegisterFrame(BaseModel):
    """Machine registers itself with the server on connect."""

    type: Literal["register"] = "register"
    machine_id: str
    capabilities: list[dict] = Field(default_factory=list)
    labels: dict = Field(default_factory=dict)


class AgentActual(BaseModel):
    """Snapshot of a single agent's current state on this machine."""

    agent_id: str
    actual_state: Literal["running", "stopped", "crashed", "starting"]
    pid: int | None = None
    engine: str = ""
    generation: int = 0
    uptime_seconds: int = 0
    last_crash_reason: str | None = None


class ReportActualStateFrame(BaseModel):
    """Machine reports the actual state of all agents it manages.

    Sent periodically or in response to a SyncBatchFrame to let the server
    reconcile desired vs actual state.
    """

    type: Literal["report_actual_state"] = "report_actual_state"
    agents: list[AgentActual] = Field(default_factory=list)


class TokenRequestFrame(BaseModel):
    """Machine requests authentication tokens for one or more agents.

    The server responds with TokenGrantFrame(s) for each requested agent.
    """

    type: Literal["token_request"] = "token_request"
    agent_ids: list[str] = Field(default_factory=list)


class RequestReplacementFrame(BaseModel):
    """Machine requests that an agent be rescheduled elsewhere.

    Sent when the machine cannot keep an agent running (e.g., hardware
    issues, crash loops) and wants the server to place it on another machine.
    """

    type: Literal["request_replacement"] = "request_replacement"
    agent_id: str
    reason: str = ""


MachineFrame = Union[
    RegisterFrame,
    ReportActualStateFrame,
    TokenRequestFrame,
    RequestReplacementFrame,
]


# ── Frame parsing ─────────────────────────────────────────────────────

_SERVER_FRAME_MAP: dict[str, type[BaseModel]] = {
    "sync_desired_state": SyncDesiredStateFrame,
    "sync_batch": SyncBatchFrame,
    "token_grant": TokenGrantFrame,
    "drain": DrainFrame,
    "ping": PingFrame,
    "rotate_token": RotateTokenFrame,
}


def parse_server_frame(data: dict) -> ServerFrame:
    """Parse a raw dict from server into the appropriate frame model.

    Raises ValueError if the frame type is unknown.
    """
    frame_type = data.get("type")
    if frame_type not in _SERVER_FRAME_MAP:
        raise ValueError(f"Unknown server frame type: {frame_type!r}")
    return _SERVER_FRAME_MAP[frame_type].model_validate(data)
