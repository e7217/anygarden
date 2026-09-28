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
