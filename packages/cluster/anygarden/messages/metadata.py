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


def strip_turn_proof(
    metadata: dict[str, Any], *, keep_request_id: bool = False
) -> dict[str, Any]:
    """Only durable delivery may supply execution proof to a recipient.

    Authenticated WS agent completions retain their historical request ID for
    tracing after the completion validator consumes the actual lease proof.
    """
    return {
        key: value
        for key, value in metadata.items()
        if key not in TURN_PROOF_METADATA_KEYS
        or (keep_request_id and key == "request_id")
    }
