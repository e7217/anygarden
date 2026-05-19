"""Unread-update helpers for sidebar room indicators (#385)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Message, Participant


async def compute_has_updates_map(
    db: AsyncSession,
    *,
    user_id: str,
    room_ids: list[str],
) -> dict[str, bool]:
    """Return ``room_id -> has_updates`` for the caller's memberships.

    ``last_read_message_seq`` is user/room scoped on ``Participant``.
    A room has updates when the latest message seq is greater than the
    stored read seq. NULL read seq means "never read"; it only lights
    up when the room actually has messages.
    """
    if not room_ids:
        return {}

    participant_rows = (
        await db.execute(
            select(
                Participant.room_id,
                Participant.last_read_message_seq,
            ).where(
                Participant.user_id == user_id,
                Participant.room_id.in_(room_ids),
            )
        )
    ).all()
    read_by_room = {row.room_id: row.last_read_message_seq for row in participant_rows}
    if not read_by_room:
        return {}

    latest_rows = (
        await db.execute(
            select(Message.room_id, func.max(Message.seq).label("max_seq"))
            .where(Message.room_id.in_(read_by_room.keys()))
            .group_by(Message.room_id)
        )
    ).all()
    latest_by_room = {row.room_id: row.max_seq for row in latest_rows}

    out: dict[str, bool] = {}
    for room_id, last_read in read_by_room.items():
        latest = latest_by_room.get(room_id)
        if latest is None:
            out[room_id] = False
        elif last_read is None:
            out[room_id] = True
        else:
            out[room_id] = int(last_read) < int(latest)
    return out


async def mark_room_read(
    db: AsyncSession,
    *,
    user_id: str,
    room_id: str,
) -> int | None:
    """Advance the user's read cursor to the room's latest message seq.

    Returns the resulting read seq, or ``None`` when the user is not a
    participant or the room has no messages. The update is monotonic:
    a stale caller cannot move the cursor backwards.
    """
    participant = (
        await db.execute(
            select(Participant).where(
                Participant.room_id == room_id,
                Participant.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if participant is None:
        return None

    latest = (
        await db.execute(
            select(func.max(Message.seq)).where(Message.room_id == room_id)
        )
    ).scalar_one_or_none()
    if latest is None:
        return participant.last_read_message_seq

    latest_int = int(latest)
    current = participant.last_read_message_seq
    if current is None or int(current) < latest_int:
        participant.last_read_message_seq = latest_int
        await db.flush()
        return latest_int
    return int(current)
