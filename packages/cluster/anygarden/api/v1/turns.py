"""Operator surface for durable agent-turn recovery state."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import AgentTurn, AgentTurnAttempt, Task
from anygarden.dependencies import get_admin_identity, get_current_identity, get_db
from anygarden.rooms.authorization import (
    Capability,
    accessible_room_ids,
    require_capability,
)

router = APIRouter(prefix="/api/v1/turns", tags=["turns"])


class TurnOut(BaseModel):
    request_id: str
    room_id: str
    agent_id: str | None
    participant_id: str | None
    task_id: str | None
    state: str
    protocol_version: int
    idempotency_key: str
    active_attempt: int
    retry_count: int
    max_retries: int
    attempt_state: str | None
    generation: int | None
    lease_expires_at: str | None
    terminal_reason: str | None
    accepted_message_id: str | None
    created_at: str
    completed_at: str | None


@router.get("", response_model=list[TurnOut])
async def list_turns(
    state: str | None = None,
    agent_id: str | None = None,
    room_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> list[TurnOut]:
    del identity
    stmt = (
        select(AgentTurn, AgentTurnAttempt)
        .outerjoin(
            AgentTurnAttempt,
            (AgentTurnAttempt.turn_id == AgentTurn.request_id)
            & (AgentTurnAttempt.attempt_number == AgentTurn.active_attempt),
        )
        .order_by(AgentTurn.created_at.desc())
        .limit(limit)
    )
    if state is not None:
        stmt = stmt.where(AgentTurn.state == state)
    if agent_id is not None:
        stmt = stmt.where(AgentTurn.agent_id == agent_id)
    if room_id is not None:
        stmt = stmt.where(AgentTurn.room_id == room_id)
    rows = (await db.execute(stmt)).all()
    return [
        TurnOut(
            request_id=turn.request_id,
            room_id=turn.room_id,
            agent_id=turn.agent_id,
            participant_id=turn.target_participant_id,
            task_id=turn.task_id,
            state=turn.state,
            protocol_version=turn.protocol_version,
            idempotency_key=turn.idempotency_key,
            active_attempt=turn.active_attempt,
            retry_count=turn.retry_count,
            max_retries=turn.max_retries,
            attempt_state=attempt.state if attempt is not None else None,
            generation=attempt.generation if attempt is not None else None,
            lease_expires_at=(
                attempt.lease_expires_at.isoformat()
                if attempt is not None and attempt.lease_expires_at is not None
                else None
            ),
            terminal_reason=turn.terminal_reason,
            accepted_message_id=turn.accepted_message_id,
            created_at=turn.created_at.isoformat(),
            completed_at=(turn.completed_at.isoformat() if turn.completed_at else None),
        )
        for turn, attempt in rows
    ]


@router.get("/summary")
async def turn_summary(
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    del identity
    rows = (
        await db.execute(
            select(AgentTurn.state, func.count()).group_by(AgentTurn.state)
        )
    ).all()
    counts = {state: int(count) for state, count in rows}
    return {
        "counts": {
            state: counts.get(state, 0)
            for state in (
                "pending",
                "leased",
                "retrying",
                "completing",
                "completed",
                "cancelled",
                "failed",
            )
        }
    }


class OpenTurnOut(BaseModel):
    request_id: str
    room_id: str
    agent_id: str | None
    participant_id: str | None
    # Root of the thread the turn answers in, or None for the main
    # timeline — lets the UI show a thread's work inside that thread.
    thread_root_id: str | None
    state: str
    created_at: str


@router.get("/open", response_model=list[OpenTurnOut])
async def list_open_room_turns(
    room_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> list[OpenTurnOut]:
    """ENG-09 — open general (non-execution) turns a room member may cancel."""
    from anygarden.turns.service import OPEN_TURN_STATES

    await require_capability(db, room_id=room_id, identity=identity, capability=Capability.ROOM_READ)
    rows = (await db.scalars(
        select(AgentTurn)
        .outerjoin(Task, Task.id == AgentTurn.task_id)
        .where(
            AgentTurn.room_id == room_id,
            AgentTurn.state.in_(OPEN_TURN_STATES),
            AgentTurn.execution_id.is_(None),
            Task.execution_id.is_(None),
        )
        .order_by(AgentTurn.created_at)
        .limit(100)
    )).all()
    return [
        OpenTurnOut(
            request_id=turn.request_id, room_id=turn.room_id, agent_id=turn.agent_id,
            participant_id=turn.target_participant_id,
            thread_root_id=turn.thread_root_id, state=turn.state,
            created_at=turn.created_at.isoformat(),
        )
        for turn in rows
    ]


# MSG-09 — states where an engine has taken the turn. ``pending`` is left
# out: it lingers while an agent is offline, which would read as working.
ACTIVE_TURN_STATES = ("leased", "retrying", "completing")


class ActiveTurnOut(BaseModel):
    room_id: str
    agent_ids: list[str]


@router.get("/active", response_model=list[ActiveTurnOut])
async def list_active_turns(
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> list[ActiveTurnOut]:
    """MSG-09 — rooms the caller can see where an agent is answering.

    Polled by the sidebar, so it covers the same rooms as
    ``GET /api/v1/rooms`` and includes execution-bound turns (this asks
    "is someone working", not "what may I cancel" like ``/open``).
    """
    room_ids = await accessible_room_ids(
        db,
        identity=identity,
        scope="rooms.collection",
        channel_service=getattr(request.app.state, "channel_service", None),
        include_shared=True,
    )
    if not room_ids:
        return []
    rows = (await db.execute(
        select(AgentTurn.room_id, AgentTurn.agent_id)
        .where(
            AgentTurn.room_id.in_(room_ids),
            AgentTurn.state.in_(ACTIVE_TURN_STATES),
        )
        .distinct()
    )).all()
    by_room: dict[str, set[str]] = {}
    for room_id, agent_id in rows:
        agents = by_room.setdefault(room_id, set())
        if agent_id is not None:
            agents.add(agent_id)
    return [
        ActiveTurnOut(room_id=room_id, agent_ids=sorted(agents))
        for room_id, agents in sorted(by_room.items())
    ]


@router.post("/{request_id}/cancel")
async def cancel_turn(
    request_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """ENG-09 — cancel an open turn in a general room.

    Requires message-send rights in the turn's room. Execution-bound turns
    are rejected with ``TURN_BOUND_TO_EXECUTION``; use
    ``POST /api/v1/executions/{id}/cancel`` for those. Re-cancelling a
    cancelled turn is a no-op; a completed/failed turn is a 409.
    """
    from anygarden.turns.cancel import (
        TurnCancelError,
        cancel_general_turn,
        load_turn,
        notify_turn_cancelled,
    )

    if identity.kind != "user":
        raise HTTPException(403, detail={"code": "TURN_CANCEL_USER_ONLY",
                                         "detail": "Only room members can cancel a turn"})
    try:
        turn = await load_turn(db, request_id)
        await require_capability(db, room_id=turn.room_id, identity=identity,
                                 capability=Capability.MESSAGE_SEND)
        result = await cancel_general_turn(db, request_id, actor_user_id=identity.id)
    except TurnCancelError as exc:
        await db.rollback()
        raise HTTPException(exc.status, detail={"code": exc.code, "detail": exc.detail}) from exc
    await db.commit()
    stop_sent = False
    if result.changed:
        stop_sent = await notify_turn_cancelled(
            getattr(request.app.state, "connection_manager", None), result.turn, result.stop_target,
        )
    return {
        "request_id": result.turn.request_id,
        "room_id": result.turn.room_id,
        "state": result.turn.state,
        "terminal_reason": result.turn.terminal_reason,
        "changed": result.changed,
        "stop_sent": stop_sent,
    }
