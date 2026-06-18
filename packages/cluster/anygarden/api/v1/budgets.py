"""Admin CRUD for token-budget policies — ``/api/v1/budgets`` (#453).

Admin-only surface (``get_admin_identity``) over
:class:`~anygarden.db.models.TokenBudgetPolicy`. Mirrors the
``/api/v1/llm-gateway`` router style: pydantic schemas with
``extra="forbid"``, ``from_attributes`` output, and the same dependency
gate.

The gate these policies drive lives in
``anygarden.budgets.ledger.evaluate_invocation_block`` and is consulted
by the LLM gateway reverse proxy. A policy is inert until both
``is_active`` and ``hard_stop_enabled`` are true — created policies
default to ``hard_stop_enabled=False`` (the no-op default that keeps this
feature behaviour-neutral until an operator deliberately switches it on).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, TokenBudgetIncident, TokenBudgetPolicy
from anygarden.dependencies import get_admin_identity, get_db

router = APIRouter(prefix="/api/v1/budgets", tags=["token-budgets"])


ScopeType = Literal["global", "agent", "room"]
WindowKind = Literal["rolling_24h", "calendar_day_utc"]


# ── Schemas ────────────────────────────────────────────────────────────


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: ScopeType
    # NULL for global; required (non-empty) for agent / room.
    scope_id: Optional[str] = Field(default=None, max_length=36)
    token_ceiling: int = Field(..., ge=1)
    warn_percent: int = Field(default=80, ge=1, le=100)
    window_kind: WindowKind = "rolling_24h"
    hard_stop_enabled: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def _check_scope_id(self) -> "PolicyCreate":
        if self.scope_type == "global":
            # A global policy spans all usage — a scope_id would be
            # meaningless and is silently dropped to NULL.
            object.__setattr__(self, "scope_id", None)
        elif not (self.scope_id or "").strip():
            raise ValueError(
                f"scope_type '{self.scope_type}' requires a non-empty scope_id"
            )
        return self


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # scope_type/scope_id are intentionally immutable post-create — a
    # policy is identified by its scope. Only the tunables change.
    token_ceiling: Optional[int] = Field(default=None, ge=1)
    warn_percent: Optional[int] = Field(default=None, ge=1, le=100)
    window_kind: Optional[WindowKind] = None
    hard_stop_enabled: Optional[bool] = None
    is_active: Optional[bool] = None


class PolicyOut(BaseModel):
    id: str
    scope_type: str
    scope_id: Optional[str]
    token_ceiling: int
    warn_percent: int
    window_kind: str
    hard_stop_enabled: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── CRUD ───────────────────────────────────────────────────────────────


@router.get("", response_model=list[PolicyOut])
async def list_policies(
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> list[TokenBudgetPolicy]:
    rows = (
        await db.execute(
            select(TokenBudgetPolicy).order_by(
                TokenBudgetPolicy.scope_type,
                TokenBudgetPolicy.created_at,
            )
        )
    ).scalars().all()
    return list(rows)


@router.post("", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
async def create_policy(
    body: PolicyCreate,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> TokenBudgetPolicy:
    row = TokenBudgetPolicy(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        token_ceiling=body.token_ceiling,
        warn_percent=body.warn_percent,
        window_kind=body.window_kind,
        hard_stop_enabled=body.hard_stop_enabled,
        is_active=body.is_active,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.patch("/{policy_id}", response_model=PolicyOut)
async def update_policy(
    policy_id: str,
    body: PolicyUpdate,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> TokenBudgetPolicy:
    row = await db.get(TokenBudgetPolicy, policy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: str,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await db.get(TokenBudgetPolicy, policy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.delete(row)
    await db.commit()


# ── Active-stop resume (#455, Wave 2a) ──────────────────────────────────


class ResumeOut(BaseModel):
    """Result of resuming a budget-paused agent."""

    agent_id: str
    incidents_resolved: int


@router.post("/agents/{agent_id}/resume", response_model=ResumeOut)
async def resume_agent(
    agent_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> ResumeOut:
    """Lift a budget arrest on an agent and restart it (admin).

    Reverses ``evaluate_cost_event``'s active-stop side-effects for an
    AGENT-scope hard breach:

    1. Clear ``pause_reason`` (back to NULL) so the invocation-block
       short-circuit no longer fires for residual calls.
    2. Resolve every *open* incident for this agent scope — stamping
       ``resolved`` + ``resolved_at`` so the same window doesn't keep the
       agent flagged.
    3. ``request_start`` the agent through the normal lifecycle path
       (placement / generation / machine sync), rather than flipping
       desired_state by hand.

    Note: an admin who resumes while the window is still over ceiling and
    the agent keeps spending will be re-stopped by the next
    ``evaluate_cost_event`` — that's intended; resume is "I've handled
    it / raised the ceiling", not a permanent override.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.pause_reason = None

    open_incidents = (
        await db.execute(
            select(TokenBudgetIncident).where(
                TokenBudgetIncident.scope_type == "agent",
                TokenBudgetIncident.scope_id == agent_id,
                TokenBudgetIncident.status == "open",
            )
        )
    ).scalars().all()
    resolved_at = datetime.now(timezone.utc)
    for incident in open_incidents:
        incident.status = "resolved"
        incident.resolved_at = resolved_at
    await db.commit()

    lifecycle = getattr(request.app.state, "agent_lifecycle", None)
    if lifecycle is not None:
        await lifecycle.request_start(agent_id)

    return ResumeOut(
        agent_id=agent_id,
        incidents_resolved=len(open_incidents),
    )
