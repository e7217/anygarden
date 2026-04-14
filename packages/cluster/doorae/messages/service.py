"""Message service — append and paginate, integrating with the repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Message
from doorae.db.repository import append_message as _repo_append, replay_since_seq


async def append_message(
    db: AsyncSession,
    room_id: str,
    participant_id: str,
    content: str,
    metadata: dict | None = None,
) -> Message:
    """Persist a new message and return it with the assigned seq."""
    return await _repo_append(db, room_id, participant_id, content, metadata)


async def get_message_history(
    db: AsyncSession,
    room_id: str,
    since_seq: int = 0,
    limit: int = 50,
) -> list[Message]:
    """Return paginated messages for a room, ordered by seq ascending.

    If *since_seq* is 0, returns the latest *limit* messages.
    Otherwise returns messages with seq > since_seq.
    """
    if since_seq > 0:
        return await replay_since_seq(db, room_id, since_seq, limit)

    # Return last `limit` messages (most recent first, then reverse)
    result = await db.execute(
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(Message.seq.desc())
        .limit(limit)
    )
    messages = list(result.scalars().all())
    messages.reverse()
    return messages
