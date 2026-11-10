"""Agent links and question chains (#1006).

``/api/v1/agent-links`` edits whom each agent may ask from any room. Any
signed-in person may edit links for now — unlike ``/api/v1/agents``, which
is admin-only — until per-user management exists.

``/api/v1/rooms/{room_id}/peer-chains`` shows how a person's message spread
through ``ask_peer`` (who asked whom, what is still running) and stops a
whole chain.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, AgentLink
from anygarden.dependencies import get_current_identity, get_db
from anygarden.orchestration.agent_links import MAX_WHEN_TO_ASK_CHARS
from anygarden.rooms.authorization import Capability, require_capability
from anygarden.rooms.roster import broadcast_roster_for_agent

router = APIRouter(tags=["agent-links"])


class AgentBriefOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    actual_state: str | None = None


class AgentLinkOut(BaseModel):
    id: str
    from_agent_id: str
    to_agent_id: str
    when_to_ask: str
    created_at: str | None = None
    updated_at: str | None = None


class AgentLinksOut(BaseModel):
    agents: list[AgentBriefOut]
    links: list[AgentLinkOut]


class AgentLinkCreate(BaseModel):
    from_agent_id: str
    to_agent_id: str
    when_to_ask: str


class AgentLinkUpdate(BaseModel):
    when_to_ask: str


def _require_person(identity: Identity) -> None:
    if identity.kind != "user":
        raise HTTPException(403, detail="Only people can edit agent links")


def _clean_when(value: str) -> str:
    text = value.strip()
    if not text or len(text) > MAX_WHEN_TO_ASK_CHARS:
        raise HTTPException(
            422,
            detail=f"when_to_ask must be 1–{MAX_WHEN_TO_ASK_CHARS} characters",
        )
    return text


def _out(link: AgentLink) -> AgentLinkOut:
    return AgentLinkOut(
        id=link.id,
        from_agent_id=link.from_agent_id,
        to_agent_id=link.to_agent_id,
        when_to_ask=link.when_to_ask,
        created_at=link.created_at.isoformat() if link.created_at else None,
        updated_at=link.updated_at.isoformat() if link.updated_at else None,
    )


async def _refresh_rosters(request: Request, db: AsyncSession, agent_id: str) -> None:
    """The asking agent's rooms carry its links in their roster."""
    manager = getattr(request.app.state, "connection_manager", None)
    await broadcast_roster_for_agent(manager, db, agent_id=agent_id)


@router.get("/api/v1/agent-links", response_model=AgentLinksOut)
async def list_agent_links(
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> AgentLinksOut:
    _require_person(identity)
    agents = (await db.scalars(select(Agent).order_by(Agent.name, Agent.id))).all()
    links = (
        await db.scalars(select(AgentLink).order_by(AgentLink.created_at, AgentLink.id))
    ).all()
    return AgentLinksOut(
        agents=[
            AgentBriefOut(
                id=a.id,
                name=a.name or "",
                description=a.description,
                actual_state=a.actual_state,
            )
            for a in agents
        ],
        links=[_out(link) for link in links],
    )


@router.post("/api/v1/agent-links", response_model=AgentLinkOut, status_code=201)
async def create_agent_link(
    body: AgentLinkCreate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> AgentLinkOut:
    _require_person(identity)
    when = _clean_when(body.when_to_ask)
    if body.from_agent_id == body.to_agent_id:
        raise HTTPException(400, detail="An agent cannot be linked to itself")
    for agent_id in (body.from_agent_id, body.to_agent_id):
        if await db.get(Agent, agent_id) is None:
            raise HTTPException(404, detail=f"Agent {agent_id} not found")
    link = AgentLink(
        from_agent_id=body.from_agent_id,
        to_agent_id=body.to_agent_id,
        when_to_ask=when,
        created_by=identity.id,
    )
    db.add(link)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, detail="These agents are already linked") from exc
    await db.refresh(link)
    await _refresh_rosters(request, db, link.from_agent_id)
    return _out(link)


@router.patch("/api/v1/agent-links/{link_id}", response_model=AgentLinkOut)
async def update_agent_link(
    link_id: str,
    body: AgentLinkUpdate,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> AgentLinkOut:
    _require_person(identity)
    link = await db.get(AgentLink, link_id)
    if link is None:
        raise HTTPException(404, detail="Link not found")
    link.when_to_ask = _clean_when(body.when_to_ask)
    await db.commit()
    await db.refresh(link)
    await _refresh_rosters(request, db, link.from_agent_id)
    return _out(link)


@router.delete("/api/v1/agent-links/{link_id}", status_code=204)
async def delete_agent_link(
    link_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _require_person(identity)
    link = await db.get(AgentLink, link_id)
    if link is None:
        raise HTTPException(404, detail="Link not found")
    from_agent_id = link.from_agent_id
    await db.delete(link)
    await db.commit()
    await _refresh_rosters(request, db, from_agent_id)
    return Response(status_code=204)


@router.get("/api/v1/rooms/{room_id}/peer-chains")
async def list_peer_chains(
    room_id: str,
    limit: int = Query(20, ge=1, le=100),
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from anygarden.orchestration.peer_chains import list_chains

    await require_capability(
        db, room_id=room_id, identity=identity, capability=Capability.ROOM_READ
    )
    return {"chains": await list_chains(db, room_id=room_id, limit=limit)}


@router.post("/api/v1/rooms/{room_id}/peer-chains/{chain_id}/cancel")
async def cancel_peer_chain(
    room_id: str,
    chain_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Stop every open turn of a question chain; nobody is woken after."""
    from anygarden.orchestration.peer_chains import cancel_chain
    from anygarden.turns.cancel import notify_turn_cancelled

    if identity.kind != "user":
        raise HTTPException(403, detail="Only room members can stop a chain")
    await require_capability(
        db, room_id=room_id, identity=identity, capability=Capability.MESSAGE_SEND
    )
    result = await cancel_chain(
        db, room_id=room_id, chain_id=chain_id, actor_user_id=identity.id
    )
    if result is None:
        raise HTTPException(404, detail="Chain not found")
    await db.commit()
    manager = getattr(request.app.state, "connection_manager", None)
    for item in result.cancelled:
        await notify_turn_cancelled(manager, item.turn, item.stop_target)
    return {"cancelled_turns": len(result.cancelled)}
