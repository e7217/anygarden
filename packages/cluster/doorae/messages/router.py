"""REST endpoint for message history — ``/api/v1/rooms/{id}/messages``."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.dependencies import get_current_identity, get_db
from doorae.messages.service import get_message_history

router = APIRouter(prefix="/api/v1/rooms", tags=["messages"])


class MessageOut(BaseModel):
    id: str
    room_id: str
    # None when the sender has been removed from the room (FK ON DELETE SET NULL).
    # Frontend renders these as "(left the room)".
    participant_id: Optional[str] = None
    content: str
    seq: int
    created_at: datetime
    extra_metadata: Optional[dict[str, Any]] = None
    model_config = {"from_attributes": True}


@router.get("/{room_id}/messages", response_model=list[MessageOut])
async def list_messages(
    room_id: str,
    since_seq: int = 0,
    limit: int = 50,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated message history for a room.

    - ``since_seq=N`` returns messages with seq > N
    - ``limit`` caps the result set (default 50, max 200)
    """
    limit = min(limit, 200)
    messages = await get_message_history(db, room_id, since_seq, limit)
    return messages
