"""REST message log, root timeline, and direct-thread endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.dependencies import get_current_identity, get_db
from anygarden.messages.references import (
    InvalidSharedFileReference,
    canonicalize_shared_file_references,
)
from anygarden.messages.serialization import message_to_frame
from anygarden.messages.service import (
    append_message,
    get_message_history,
    get_thread_messages,
    get_thread_roots,
)
from anygarden.orchestration.rules import parse_mentions
from anygarden.rooms.authorization import Capability, require_capability

router = APIRouter(prefix="/api/v1/rooms", tags=["messages"])


class MessageOut(BaseModel):
    id: str
    room_id: str
    # None when the sender has been removed from the room (FK ON DELETE SET NULL).
    # Frontend renders these as "(left the room)".
    participant_id: Optional[str] = None
    content: str
    parent_message_id: Optional[str] = None
    root_message_id: Optional[str] = None
    seq: int
    created_at: datetime
    # Issue #61 — DB column is ``extra_metadata`` but the frontend and
    # WS payload both expose this as ``metadata``. ``serialization_alias``
    # makes the JSON key ``metadata`` on the wire while keeping
    # ``extra_metadata`` as the Python attribute name (required for
    # ``from_attributes=True`` to map the ORM column). Without this
    # alias, page refresh would serve history with ``extra_metadata`` and
    # the frontend's ``room_query``/``room_query_forward`` cards would
    # silently fall back to plain bubbles.
    extra_metadata: Optional[dict[str, Any]] = Field(
        default=None, serialization_alias="metadata"
    )
    model_config = {"from_attributes": True, "populate_by_name": True}


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    metadata: Optional[dict[str, Any]] = None


async def _read_access(
    db: AsyncSession,
    *,
    room_id: str,
    identity: Identity,
):
    return await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.ROOM_READ,
    )


async def _write_message(
    *,
    request: Request,
    db: AsyncSession,
    room_id: str,
    identity: Identity,
    body: MessageCreate,
    thread_root_id: str | None = None,
):
    access = await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.MESSAGE_SEND,
    )
    metadata = dict(body.metadata) if body.metadata else {}
    try:
        metadata = await canonicalize_shared_file_references(
            db,
            room_id=room_id,
            metadata=metadata,
            allow_shared_files=identity.kind != "guest",
        )
    except InvalidSharedFileReference as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid shared file reference",
        ) from exc
    # Mentions are server-derived routing data, never client authority. Drop
    # caller-supplied values even when the canonical parser finds no mentions.
    metadata.pop("mentions", None)
    mentions = parse_mentions(body.content)
    if identity.kind == "guest":
        mentions = [
            mention for mention in mentions if mention.get("type") in {"user", "legacy"}
        ]
    if mentions:
        metadata["mentions"] = mentions

    message = await append_message(
        db,
        room_id=room_id,
        participant_id=(access.participant.id if access.participant else None),
        content=body.content,
        metadata=metadata or None,
        thread_root_id=thread_root_id,
    )
    await db.commit()

    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        await manager.broadcast(room_id, message_to_frame(message))
    return message


@router.get("/{room_id}/messages", response_model=list[MessageOut])
async def list_messages(
    room_id: str,
    since_seq: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return the compatible append-only log of roots and replies.

    ``since_seq=N`` returns messages with seq > N. With no cursor, the latest
    page is returned in ascending order. Authorization uses the shared room
    read capability so guest room binding and private-room membership remain
    identical to every other room read surface.
    """

    await _read_access(db, room_id=room_id, identity=identity)
    return await get_message_history(db, room_id, since_seq, limit)


@router.get("/{room_id}/thread-roots", response_model=list[MessageOut])
async def list_thread_roots(
    room_id: str,
    since_seq: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    await _read_access(db, room_id=room_id, identity=identity)
    return await get_thread_roots(db, room_id, since_seq, limit)


@router.get(
    "/{room_id}/threads/{root_message_id}/messages",
    response_model=list[MessageOut],
)
async def list_thread_messages(
    room_id: str,
    root_message_id: str,
    since_seq: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    await _read_access(db, room_id=room_id, identity=identity)
    return await get_thread_messages(
        db,
        room_id,
        root_message_id,
        since_seq,
        limit,
    )


@router.post(
    "/{room_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_root_message(
    room_id: str,
    body: MessageCreate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    return await _write_message(
        request=request,
        db=db,
        room_id=room_id,
        identity=identity,
        body=body,
    )


@router.post(
    "/{room_id}/threads/{root_message_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread_reply(
    room_id: str,
    root_message_id: str,
    body: MessageCreate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    return await _write_message(
        request=request,
        db=db,
        room_id=room_id,
        identity=identity,
        body=body,
        thread_root_id=root_message_id,
    )
