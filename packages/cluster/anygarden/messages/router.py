"""REST message log, root timeline, and direct-thread endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Message as MessageRow
from anygarden.db.models import MessageReaction, Participant
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


class ReactionCreate(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)


def _reaction_frame(message_id: str, participant_id: str, emoji: str, action: str):
    """Receipt event, deliberately NOT a message frame: reaction events are
    structurally excluded from agent wake paths (D-1 attention norm)."""
    return {
        "type": "reaction",
        "room_id": None,
        "message_id": message_id,
        "participant_id": participant_id,
        "emoji": emoji,
        "action": action,
    }


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
    # D-6 (#629): interactions are local-first — the only shared-room send
    # exemption. They stay ordinary room messages (never channel commands),
    # so they survive a missing home node while channel-wide writes remain
    # blocked per #591.
    from anygarden.interactions import InteractionSchemaError, is_interaction_send

    allow_shared = is_interaction_send(body.metadata)
    access = await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.MESSAGE_SEND,
        allow_shared=allow_shared,
    )
    room_wake_triggers = list(getattr(access.room, "wake_triggers", None) or [])
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

    # D-1 (#624) — server-stamped wake classification. "mention" when the
    # message addresses someone; "message" only when the room opted in to
    # plain-message wakes; otherwise NO stamp — the frame still delivers to
    # humans/agents, but agents fall back to the legacy judgment chain
    # (backward compatibility, architect condition 4).
    if mentions:
        metadata["wake_trigger"] = "mention"
    elif room_wake_triggers and "message" in room_wake_triggers:
        metadata["wake_trigger"] = "message"

    if allow_shared or any(k in metadata for k in ("interaction", "interaction_resolution")):
        from anygarden.interactions import LookupError as _Lookup  # noqa: F401
        from anygarden.interactions import process_send

        try:
            metadata = await process_send(
                db,
                metadata,
                thread_root_id=thread_root_id,
                sender_participant_id=(
                    access.participant.id if access.participant else None
                ),
            )
        except (InteractionSchemaError, LookupError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=getattr(exc, "detail", str(exc)),
            ) from exc

    message = await append_message(
        db,
        room_id=room_id,
        participant_id=(access.participant.id if access.participant else None),
        content=body.content,
        metadata=metadata or None,
        thread_root_id=thread_root_id,
    )
    resolution = (metadata or {}).get("interaction_resolution")
    if resolution is not None:
        from anygarden.interactions import record_resolution

        await record_resolution(
            db,
            interaction_id=resolution["interaction_id"],
            room_id=room_id,
            request_message_id=thread_root_id,
            resolution_message_id=message.id,
        )
    await db.commit()

    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        await manager.broadcast(room_id, message_to_frame(message))
    return message


async def _reaction_target(
    db: AsyncSession, *, room_id: str, message_id: str, identity: Identity
):
    """Shared preconditions for reaction endpoints: read capability, the
    caller's participant row in this room (guests and identity-less callers
    are excluded — receipts are participant-scoped), and the message must
    exist in this room."""
    await _read_access(db, room_id=room_id, identity=identity)
    if identity.kind not in {"user", "agent"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only users and agents can react",
        )
    column = Participant.user_id if identity.kind == "user" else Participant.agent_id
    participant_id = await db.scalar(
        select(Participant.id).where(
            Participant.room_id == room_id,
            column == identity.id,
        )
    )
    if participant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this room",
        )
    message = await db.get(MessageRow, message_id)
    if message is None or message.room_id != room_id:
        raise HTTPException(status_code=404, detail="Message not found")
    return participant_id


@router.post(
    "/{room_id}/messages/{message_id}/reactions",
    status_code=status.HTTP_201_CREATED,
)
async def add_reaction(
    room_id: str,
    message_id: str,
    body: ReactionCreate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Emoji receipt (D-1). Broadcasts a reaction event — never a message
    frame — so reacting does not wake channel agents."""
    participant_id = await _reaction_target(
        db, room_id=room_id, message_id=message_id, identity=identity
    )
    reaction = MessageReaction(
        id=str(uuid4()),
        message_id=message_id,
        participant_id=participant_id,
        emoji=body.emoji,
    )
    db.add(reaction)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reaction already exists",
        ) from None
    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        await manager.broadcast(
            room_id,
            _reaction_frame(message_id, participant_id, body.emoji, "added"),
        )
    return {
        "message_id": message_id,
        "participant_id": participant_id,
        "emoji": body.emoji,
    }


@router.delete(
    "/{room_id}/messages/{message_id}/reactions/{emoji}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_reaction(
    room_id: str,
    message_id: str,
    emoji: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    participant_id = await _reaction_target(
        db, room_id=room_id, message_id=message_id, identity=identity
    )
    result = await db.execute(
        sa_delete(MessageReaction).where(
            MessageReaction.message_id == message_id,
            MessageReaction.participant_id == participant_id,
            MessageReaction.emoji == emoji,
        )
    )
    await db.commit()
    if result.rowcount:
        manager = getattr(request.app.state, "connection_manager", None)
        if manager is not None:
            await manager.broadcast(
                room_id,
                _reaction_frame(message_id, participant_id, emoji, "removed"),
            )


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
