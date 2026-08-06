"""Operator surface for durable agent-turn recovery state."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import AgentTurn, AgentTurnAttempt
from anygarden.dependencies import get_admin_identity, get_db

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
