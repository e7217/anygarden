"""Clear agents' engine sessions in a room (#986, ``/clear``).

A long room conversation keeps growing each agent's engine session. A room
admin or owner can start it over: the agents' next turns here run in a new
engine session, and the conversation context they get starts after the
room's "session cleared" notice. Room memory notes and the messages people
see are kept.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, Participant, User
from anygarden.dependencies import forbid_guest, get_db
from anygarden.memory.service import push_room_memory, reset_room_sessions
from anygarden.messages.serialization import message_to_frame
from anygarden.messages.service import append_message
from anygarden.rooms.authorization import (
    AGENT_EXECUTION_ROLES,
    Capability,
    require_capability,
)

SYSTEM_ORIGIN = "session_reset"

router = APIRouter(tags=["rooms"])


class SessionResetIn(BaseModel):
    """``agent_participant_id`` names one agent; omitted means all of them."""

    model_config = ConfigDict(extra="forbid")
    agent_participant_id: str | None = None


class SessionResetOut(BaseModel):
    agent_ids: list[str]
    message_id: str


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.post("/{room_id}/session-reset", response_model=SessionResetOut)
async def reset_sessions(
    room_id: str,
    body: SessionResetIn,
    request: Request,
    identity: Annotated[Identity, Depends(forbid_guest)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionResetOut:
    if identity.kind != "user":
        raise _refuse(403, "SESSION_RESET_USER_ONLY", "Only a person can clear sessions")
    access = await require_capability(
        db, room_id=room_id, identity=identity, capability=Capability.ROOM_SETTINGS_MANAGE,
    )
    if access.room.ephemeral:
        raise _refuse(400, "SESSION_RESET_EPHEMERAL", "A temporary room keeps no session to clear")

    query = (
        select(Participant, Agent)
        .join(Agent, Agent.id == Participant.agent_id)
        .where(Participant.room_id == room_id, Participant.role.in_(AGENT_EXECUTION_ROLES))
        .order_by(Agent.name)
    )
    if body.agent_participant_id is not None:
        query = query.where(Participant.id == body.agent_participant_id)
    targets = list((await db.execute(query)).all())
    if not targets:
        if body.agent_participant_id is not None:
            raise _refuse(400, "SESSION_RESET_NOT_AN_AGENT", "That participant is not an agent in this room")
        raise _refuse(400, "SESSION_RESET_NO_AGENTS", "This room has no agents")

    user = await db.get(User, identity.id)
    actor = (user.display_name or user.email or "") if user is not None else ""
    names = ", ".join(agent.name for _, agent in targets)
    message = await append_message(
        db, room_id, None,
        f"{actor} cleared the conversation session of {names}. Room memory is kept.",
        {
            "system_origin": SYSTEM_ORIGIN,
            SYSTEM_ORIGIN: {
                "actor_participant_id": access.participant.id if access.participant else None,
                "actor_name": actor,
                "agents": [
                    {"agent_id": agent.id, "participant_id": p.id, "name": agent.name}
                    for p, agent in targets
                ],
            },
        },
    )
    agent_ids = [agent.id for _, agent in targets]
    await reset_room_sessions(db, room_id=room_id, agent_ids=agent_ids, reset_seq=message.seq)
    await db.commit()

    manager = getattr(request.app.state, "connection_manager", None)
    if manager is not None:
        await manager.broadcast(room_id, message_to_frame(message))
    for agent_id in agent_ids:
        await push_room_memory(request.app, db, agent_id=agent_id, room_id=room_id, machine_snapshot=True)
    return SessionResetOut(agent_ids=agent_ids, message_id=message.id)
