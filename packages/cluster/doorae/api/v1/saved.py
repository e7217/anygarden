"""REST endpoints for saved/bookmarked messages."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.db.models import Message, Participant, SavedMessage
from doorae.dependencies import forbid_guest, get_db

router = APIRouter(prefix="/api/v1/saved", tags=["saved"])


class SaveMessageBody(BaseModel):
    message_id: str


class SavedMessageOut(BaseModel):
    id: str
    message_id: str
    room_id: str
    content: str
    participant_id: Optional[str] = None
    display_name: str = ""
    saved_at: str

    model_config = {"from_attributes": True}


@router.post("", status_code=201)
async def save_message(
    body: SaveMessageBody,
    # Saved messages are a registered-user feature (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Bookmark a message."""
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="Only users can save messages")

    msg = (await db.execute(select(Message).where(Message.id == body.message_id))).scalar_one_or_none()
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")

    existing = (await db.execute(
        select(SavedMessage).where(
            SavedMessage.user_id == identity.id,
            SavedMessage.message_id == body.message_id,
        )
    )).scalar_one_or_none()
    if existing:
        return {"id": existing.id, "message_id": existing.message_id, "already_saved": True}

    saved = SavedMessage(user_id=identity.id, message_id=body.message_id)
    db.add(saved)
    await db.commit()
    await db.refresh(saved)
    return {"id": saved.id, "message_id": saved.message_id}


@router.delete("/{message_id}", status_code=200)
async def unsave_message(
    message_id: str,
    # Saved messages are a registered-user feature (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Remove a bookmark."""
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="Only users can unsave messages")

    result = await db.execute(
        delete(SavedMessage).where(
            SavedMessage.user_id == identity.id,
            SavedMessage.message_id == message_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Saved message not found")
    return {"removed": True}


@router.get("", response_model=list[SavedMessageOut])
async def list_saved_messages(
    # Saved messages are a registered-user feature (§11.5).
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """List all bookmarked messages for the current user."""
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="Only users can list saved messages")

    stmt = (
        select(SavedMessage, Message)
        .join(Message, Message.id == SavedMessage.message_id)
        .where(SavedMessage.user_id == identity.id)
        .order_by(SavedMessage.saved_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    results = []
    for saved, msg in rows:
        display_name = ""
        if msg.participant_id:
            p = (await db.execute(select(Participant).where(Participant.id == msg.participant_id))).scalar_one_or_none()
            if p and p.agent_id:
                from doorae.db.models import Agent
                agent = (await db.execute(select(Agent).where(Agent.id == p.agent_id))).scalar_one_or_none()
                display_name = agent.name if agent else ""
            elif p and p.user_id:
                from doorae.db.models import User
                user = (await db.execute(select(User).where(User.id == p.user_id))).scalar_one_or_none()
                # Guests have no email; use the display_name they supplied
                # at invite-acceptance time. Registered users keep their
                # historical email local-part rendering.
                if user is None:
                    display_name = ""
                elif user.display_name:
                    display_name = user.display_name
                elif user.email:
                    display_name = user.email.split("@")[0]
                else:
                    display_name = "Guest"

        results.append(SavedMessageOut(
            id=saved.id,
            message_id=msg.id,
            room_id=msg.room_id,
            content=msg.content,
            participant_id=msg.participant_id,
            display_name=display_name,
            saved_at=saved.saved_at.isoformat(),
        ))
    return results
