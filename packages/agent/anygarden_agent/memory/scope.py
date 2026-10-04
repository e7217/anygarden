"""Validated room memory snapshots; legacy agent-wide notes are never context."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

SCOPE_VERSION = "room-memory-v1"


def room_memory_snapshot(value, *, room_id: str, generation: int | None = None):
    """Return a validated copy, or None for an absent/unsafe/stale scope."""
    try:
        if str(UUID(room_id)) != room_id or not isinstance(value, dict):
            return None
        if value.get("room_id") != room_id or value.get("scope_version") != SCOPE_VERSION:
            return None
        for key in ("revision", "session_epoch", "generation"):
            if type(value.get(key)) is not int or value[key] < 0:
                return None
        if generation is not None and value["generation"] != generation:
            return None
        if not isinstance(value.get("memory_md"), str):
            return None
        if len(value["memory_md"].encode("utf-8")) > 262144:
            return None
        if type(value.get("ephemeral", False)) is not bool:
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    return dict(value)


def memory_session_scope(client, room_id: str) -> str:
    """Fence both previously contaminated sessions and scoped admin edits."""
    snapshot = room_memory_snapshot(
        (getattr(client, "_room_memory", {}) or {}).get(room_id),
        room_id=room_id,
        generation=getattr(client, "_generation", None),
    )
    return json.dumps(
        [SCOPE_VERSION, snapshot["session_epoch"] if snapshot else 0],
        separators=(",", ":"),
    )


def room_session_key(client, room_id: str) -> str:
    return hashlib.sha256(
        json.dumps([room_id, memory_session_scope(client, room_id)], separators=(",", ":")).encode()
    ).hexdigest()
