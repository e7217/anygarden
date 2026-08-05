"""Canonical ORM-to-WebSocket serialization for message frames."""

from __future__ import annotations

from typing import Any

from anygarden.db.models import Message
from anygarden.ws.protocol import MessageOut

_USE_STORED_METADATA = object()


def message_to_frame(
    message: Message,
    *,
    metadata: dict[str, Any] | None | object = _USE_STORED_METADATA,
) -> MessageOut:
    """Build the one canonical wire payload for a persisted message.

    ``metadata`` may override the stored value for per-recipient transient
    fields such as ``request_id``. Thread identity always comes from the row.
    """

    wire_metadata = (
        message.extra_metadata if metadata is _USE_STORED_METADATA else metadata
    )
    return MessageOut(
        id=message.id,
        room_id=message.room_id,
        participant_id=message.participant_id,
        content=message.content,
        parent_message_id=message.parent_message_id,
        root_message_id=message.root_message_id,
        seq=message.seq,
        created_at=message.created_at,
        metadata=wire_metadata,  # type: ignore[arg-type]
    )


__all__ = ["message_to_frame"]
