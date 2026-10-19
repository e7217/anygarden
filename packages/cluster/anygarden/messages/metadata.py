"""Reserved invocation metadata for public message ingress."""

from typing import Any

TURN_PROOF_METADATA_KEYS = frozenset(
    {
        "request_id",
        "turn_attempt",
        "turn_generation",
        "turn_lease",
        "turn_protocol",
        "turn_idempotency_key",
        "workspace_attachment_id",
        "workspace_attachment_epoch",
        # #802 — set only on a leased delivery to an operating room's lead.
        "operating_lead",
    }
)


# Correlation id an agent reply carries for tracing (#719). It must not reuse
# ``request_id``: on any frame an agent receives, ``request_id`` always means
# "your own turn", and a peer reading the sender's id would try to complete a
# turn that is not its own.
REPLY_REQUEST_ID_KEY = "reply_to_request_id"


def strip_turn_proof(
    metadata: dict[str, Any], *, correlate_reply: bool = False
) -> dict[str, Any]:
    """Only durable delivery may supply execution proof to a recipient.

    Authenticated WS agent completions keep their historical request ID for
    tracing after the completion validator consumes the actual lease proof,
    but under :data:`REPLY_REQUEST_ID_KEY` so it is never mistaken for a
    recipient's own turn. Caller-supplied correlation ids are always dropped.
    """
    stripped = {
        key: value
        for key, value in metadata.items()
        if key not in TURN_PROOF_METADATA_KEYS and key != REPLY_REQUEST_ID_KEY
    }
    request_id = metadata.get("request_id")
    if correlate_reply and isinstance(request_id, str):
        stripped[REPLY_REQUEST_ID_KEY] = request_id
    return stripped


# #880 — an agent reply's narration and work counts. The agent runtime builds
# it (``progress_stage.TraceBuilder``); ingress keeps only this closed shape
# and only from agents, so nobody can forge "what an agent did".
EXECUTION_TRACE_KEY = "execution_trace"
TRACE_TALLY_KEYS = frozenset(
    {"commands", "file_changes", "file_reads", "anygarden_tools", "tools", "web_searches"}
)
_MAX_TRACE_STEPS = 50
_MAX_TRACE_NARRATION = 2_000
_MAX_TRACE_COUNT = 10_000


def _tally(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: count
        for key, count in value.items()
        if key in TRACE_TALLY_KEYS
        and type(count) is int
        and 0 < count <= _MAX_TRACE_COUNT
    }


def _trace_step(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    step: dict[str, Any] = {}
    narration = value.get("narration")
    if isinstance(narration, str) and narration.strip():
        step["narration"] = (
            narration
            if len(narration) <= _MAX_TRACE_NARRATION
            else narration[: _MAX_TRACE_NARRATION - 1] + "…"
        )
    activities = _tally(value.get("activities"))
    if activities:
        step["activities"] = activities
    return step or None


def sanitize_execution_trace(
    metadata: dict[str, Any], *, sender_is_agent: bool
) -> dict[str, Any]:
    """Drop or normalize ``execution_trace``; other keys pass through."""
    if EXECUTION_TRACE_KEY not in metadata:
        return metadata
    cleaned = {k: v for k, v in metadata.items() if k != EXECUTION_TRACE_KEY}
    trace = metadata[EXECUTION_TRACE_KEY]
    if not sender_is_agent or not isinstance(trace, dict) or trace.get("v") != 1:
        return cleaned
    totals = _tally(trace.get("totals"))
    raw_steps = trace.get("steps")
    if not totals or not isinstance(raw_steps, list):
        return cleaned
    steps = [step for step in map(_trace_step, raw_steps) if step is not None]
    dropped = max(0, len(steps) - _MAX_TRACE_STEPS)
    truncated = trace.get("truncated")
    truncated = truncated if type(truncated) is int and truncated >= 0 else 0
    cleaned[EXECUTION_TRACE_KEY] = {
        "v": 1,
        "steps": steps[dropped:],
        "totals": totals,
        "truncated": truncated + dropped,
    }
    return cleaned
