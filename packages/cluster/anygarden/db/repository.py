"""Data-access helpers for the message log."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Message

_MAX_SEQ_RETRIES = 3


async def append_message(
    db: AsyncSession,
    room_id: str,
    participant_id: str | None,
    content: str,
    metadata: dict | None = None,
) -> Message:
    """Persist a new message with an auto-assigned room-scoped sequence number.

    Uses a retry loop to handle concurrent seq collisions protected by the
    ``uq_room_seq`` unique constraint.

    ``participant_id`` is nullable at the schema level — synthetic
    server-side injections (e.g. task assignment, #266) can pass
    ``None`` to denote a system-origin message.
    """
    for attempt in range(_MAX_SEQ_RETRIES):
        # Compute next seq for this room
        result = await db.execute(
            select(func.coalesce(func.max(Message.seq), 0)).where(
                Message.room_id == room_id
            )
        )
        next_seq = result.scalar_one() + 1

        msg = Message(
            room_id=room_id,
            participant_id=participant_id,
            content=content,
            extra_metadata=metadata,
            seq=next_seq,
        )
        db.add(msg)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            if attempt == _MAX_SEQ_RETRIES - 1:
                raise
            continue
        await db.refresh(msg)
        return msg

    # Should never reach here due to the raise above, but satisfy type checker
    raise RuntimeError("Failed to append message after retries")


async def replay_since_seq(
    db: AsyncSession,
    room_id: str,
    since_seq: int,
    limit: int = 50,
) -> list[Message]:
    """Return messages in *room_id* with ``seq > since_seq``, ordered by seq."""
    result = await db.execute(
        select(Message)
        .where(Message.room_id == room_id, Message.seq > since_seq)
        .order_by(Message.seq.asc())
        .limit(limit)
    )
    return list(result.scalars().all())
