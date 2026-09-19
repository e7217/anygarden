"""Structured human-interaction points (D-6, #629).

Four typed intervention points — question, confirmation, checklist,
judgment — expressed as closed message metadata and routed through the
D-1 wake/ingest stamps. Interactions are **local-first**: they are
ordinary room messages, never channel commands, so they keep working in
a mirror room whose authority (home) node is unreachable — that is the
explicit no-home fallback policy. The federation wire contract is
untouched.
"""

from __future__ import annotations

import re
from uuid import UUID

KINDS = ("question", "confirmation", "checklist", "judgment")
REQUEST_KEY = "interaction"
RESOLUTION_KEY = "interaction_resolution"
_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


class InteractionSchemaError(ValueError):
    """Malformed interaction metadata — the send is rejected (400)."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _uuid(value, field: str) -> str:
    if not isinstance(value, str) or not _UUID.match(value):
        try:
            return str(UUID(value))
        except (ValueError, AttributeError, TypeError):
            raise InteractionSchemaError(f"{field} must be a UUID") from None
    return value.lower()


def _text(value, field: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InteractionSchemaError(f"{field} must be a non-empty string ≤{limit} chars")
    return value


def validate_request(raw: object) -> dict:
    """Validate an ``interaction`` request payload (closed shape)."""
    if not isinstance(raw, dict):
        raise InteractionSchemaError("interaction must be an object")
    allowed = {
        "kind",
        "id",
        "prompt",
        "target_participant_id",
        "options",
        "items",
        "timeout_seconds",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise InteractionSchemaError(f"unknown interaction fields: {sorted(unknown)}")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise InteractionSchemaError(f"kind must be one of {sorted(KINDS)}")
    out = {
        "kind": kind,
        "id": _uuid(raw.get("id"), "interaction.id"),
        "prompt": _text(raw.get("prompt"), "interaction.prompt", limit=4000),
    }
    target = raw.get("target_participant_id")
    if target is not None:
        out["target_participant_id"] = _uuid(target, "interaction.target_participant_id")
    options = raw.get("options")
    if kind == "judgment":
        if not isinstance(options, list) or not 2 <= len(options) <= 6:
            raise InteractionSchemaError("judgment requires 2..6 options")
        out["options"] = [_text(o, "option", limit=200) for o in options]
    elif options is not None:
        raise InteractionSchemaError("options only valid for judgment")
    items = raw.get("items")
    if kind == "checklist":
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise InteractionSchemaError("checklist requires 1..20 items")
        out["items"] = [_text(i, "item", limit=200) for i in items]
    elif items is not None:
        raise InteractionSchemaError("items only valid for checklist")
    timeout = raw.get("timeout_seconds")
    if timeout is not None:
        if type(timeout) is not int or not 30 <= timeout <= 86400:
            raise InteractionSchemaError("timeout_seconds must be 30..86400")
        out["timeout_seconds"] = timeout
    return out


def validate_resolution(raw: object) -> dict:
    """Validate an ``interaction_resolution`` payload (closed shape)."""
    if not isinstance(raw, dict):
        raise InteractionSchemaError("interaction_resolution must be an object")
    allowed = {"interaction_id", "answer", "selections", "confirmed"}
    unknown = set(raw) - allowed
    if unknown:
        raise InteractionSchemaError(
            f"unknown interaction_resolution fields: {sorted(unknown)}"
        )
    out = {
        "interaction_id": _uuid(raw.get("interaction_id"), "interaction_id"),
    }
    answer = raw.get("answer")
    if answer is not None:
        out["answer"] = _text(answer, "answer", limit=4000)
    selections = raw.get("selections")
    if selections is not None:
        if not isinstance(selections, list) or not all(
            type(s) is int and s >= 0 for s in selections
        ):
            raise InteractionSchemaError("selections must be non-negative integers")
        out["selections"] = selections
    confirmed = raw.get("confirmed")
    if confirmed is not None:
        if type(confirmed) is not bool:
            raise InteractionSchemaError("confirmed must be a boolean")
        out["confirmed"] = confirmed
    if answer is None and selections is None and confirmed is None:
        raise InteractionSchemaError(
            "resolution needs one of answer/selections/confirmed"
        )
    return out


async def process_send(
    db,
    metadata: dict,
    *,
    thread_root_id: str | None,
    sender_participant_id: str | None,
) -> dict:
    """Validate + stamp interaction metadata on either send path (D-6).

    Mutates ``metadata`` in place and returns it. Raises
    ``InteractionSchemaError`` (caller maps to 400 / error frame) for
    malformed payloads, and ``LookupError`` when a resolution's thread
    root is not the matching request. A second resolution of the same
    interaction raises ``InteractionSchemaError("already resolved")``
    after the row insert conflicts (caller rolls back the send).
    """
    from anygarden.db.models import InteractionResolution, Message

    request = metadata.get(REQUEST_KEY)
    resolution = metadata.get(RESOLUTION_KEY)
    if request is None and resolution is None:
        return metadata
    if request is not None and resolution is not None:
        raise InteractionSchemaError("cannot combine request and resolution")
    if request is not None:
        metadata[REQUEST_KEY] = validate_request(request)
        # "Who answers" is the explicit target field (architect condition 2);
        # wake stamps only decide WHO WAKES. Untargeted requests are absorbed
        # (INGEST_ONLY) by every agent except a human reader.
        target = metadata[REQUEST_KEY].get("target_participant_id")
        if target is None:
            metadata.setdefault("ingest_only", True)
        else:
            metadata["next_speaker_participant_id"] = target
        return metadata
    metadata[RESOLUTION_KEY] = validate_resolution(resolution)
    if not thread_root_id:
        raise InteractionSchemaError("resolution must reply to the request thread")
    root = await db.get(Message, thread_root_id)
    root_interaction = ((root.extra_metadata or {}) if root else {}).get(REQUEST_KEY)
    if (
        root is None
        or root_interaction is None
        or str(root_interaction.get("id", "")).lower()
        != metadata[RESOLUTION_KEY]["interaction_id"]
    ):
        raise LookupError("resolution thread root is not the matching request")
    existing = await db.get(
        InteractionResolution, metadata[RESOLUTION_KEY]["interaction_id"]
    )
    if existing is not None:
        raise InteractionSchemaError("interaction already resolved")
    # The row is inserted by the caller after the message append succeeds
    # (they own the transaction); we only validated here.
    metadata["next_speaker_participant_id"] = root.participant_id
    return metadata


async def record_resolution(
    db,
    *,
    interaction_id: str,
    room_id: str,
    request_message_id: str,
    resolution_message_id: str,
) -> None:
    """Persist the once-only resolution row (caller commits)."""
    from anygarden.db.models import InteractionResolution

    db.add(
        InteractionResolution(
            interaction_id=interaction_id,
            room_id=room_id,
            request_message_id=request_message_id,
            resolution_message_id=resolution_message_id,
        )
    )


def is_interaction_send(metadata: dict | None) -> bool:
    """True when the send carries an interaction payload (local-first)."""
    if not metadata:
        return False
    return REQUEST_KEY in metadata or RESOLUTION_KEY in metadata
