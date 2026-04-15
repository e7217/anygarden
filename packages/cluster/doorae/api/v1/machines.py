"""REST endpoints for Machine management — ``/api/v1/machines``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.auth.machine_token import generate_machine_token, hash_machine_token
from doorae.db.models import Agent, Machine, MachineActivityLog, MachineEngine, MachineToken
from doorae.dependencies import forbid_guest, get_db

router = APIRouter(prefix="/api/v1/machines", tags=["machines"])


# ── Request / Response schemas ───────────────────────────────────────


class MachineCreate(BaseModel):
    name: str
    hostname: str = "localhost"
    labels: Optional[dict] = None


class MachineOut(BaseModel):
    id: str
    name: str
    hostname: str
    owner_user_id: str
    status: str
    daemon_version: Optional[str] = None
    labels: Optional[dict] = None
    model_config = {"from_attributes": True}


class MachineUpdate(BaseModel):
    name: Optional[str] = None
    hostname: Optional[str] = None
    labels: Optional[dict] = None


class MachineCreateOut(MachineOut):
    """Returned on creation — includes the plaintext token (shown once)."""
    machine_token: str


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MachineCreateOut)
async def register_machine(
    body: MachineCreate,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Register a new machine and return a one-time machine token."""
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="Only users can register machines")

    machine = Machine(
        name=body.name,
        hostname=body.hostname,
        owner_user_id=identity.id,
        labels=body.labels,
    )
    db.add(machine)
    await db.flush()

    # Generate and store token
    plaintext = generate_machine_token()
    hashed, hint = hash_machine_token(plaintext)
    token_record = MachineToken(
        machine_id=machine.id,
        token_hash=hashed,
        lookup_hint=hint,
    )
    db.add(token_record)
    await db.commit()
    await db.refresh(machine)

    return MachineCreateOut(
        id=machine.id,
        name=machine.name,
        hostname=machine.hostname,
        owner_user_id=machine.owner_user_id,
        status=machine.status,
        daemon_version=machine.daemon_version,
        labels=machine.labels,
        machine_token=plaintext,
    )


@router.get("", response_model=list[MachineOut])
async def list_machines(
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """List machines owned by the current user (admin sees all)."""
    stmt = select(Machine).order_by(Machine.created_at)
    if identity.claims and not identity.claims.is_admin:
        stmt = stmt.where(Machine.owner_user_id == identity.id)
    elif identity.kind != "user":
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{machine_id}", response_model=MachineOut)
async def get_machine(
    machine_id: str,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Get a single machine's details."""
    machine = await _get_owned_machine(db, machine_id, identity)
    return machine


@router.patch("/{machine_id}", response_model=MachineOut)
async def update_machine(
    machine_id: str,
    body: MachineUpdate,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Update machine settings (name, hostname, labels)."""
    machine = await _get_owned_machine(db, machine_id, identity)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(machine, field, value)
    await db.commit()
    await db.refresh(machine)
    return machine


@router.delete("/{machine_id}", status_code=200)
async def delete_machine(
    machine_id: str,
    request: Request,
    force: bool = False,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Delete a machine, stopping running agents and disconnecting the daemon.

    By default, refuses to delete if any agents are still placed on this
    machine in a non-terminal state. Pass ``?force=true`` to forcibly stop
    all agents (sends ``kill_agent`` to the daemon) before deletion.
    """
    machine = await _get_owned_machine(db, machine_id, identity)
    machine_bus = request.app.state.machine_bus
    lifecycle = request.app.state.agent_lifecycle

    # Find any agents still placed on this machine
    result = await db.execute(
        select(Agent).where(
            Agent.placed_on_machine_id == machine_id,
            Agent.actual_state.in_(("pending", "starting", "running", "stopping")),
        )
    )
    active_agents = list(result.scalars().all())

    if active_agents and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "machine_has_active_agents",
                "agent_count": len(active_agents),
                "message": (
                    f"{len(active_agents)} agent(s) are still placed on this machine. "
                    "Stop or reassign them, or pass ?force=true to forcibly stop them."
                ),
            },
        )

    # Force stop: tell lifecycle to send kill_agent to the daemon (best-effort)
    for agent in active_agents:
        try:
            await lifecycle.request_stop(agent.id)
        except Exception:
            pass  # daemon may already be unreachable; we still proceed

    # Disconnect the daemon WS so it can no longer act on this machine
    await machine_bus.disconnect(machine_id)

    # Detach remaining agents (placed_on_machine_id has ondelete=SET NULL,
    # but doing it explicitly keeps the operation deterministic).
    await db.execute(
        select(Agent).where(Agent.placed_on_machine_id == machine_id)
    )
    for agent in active_agents:
        agent.placed_on_machine_id = None
        agent.actual_state = "stopped"
        agent.desired_state = "stopped"

    # Delete tokens, then the machine
    result = await db.execute(
        select(MachineToken).where(MachineToken.machine_id == machine_id)
    )
    for tok in result.scalars().all():
        await db.delete(tok)
    await db.delete(machine)
    await db.commit()
    return {
        "deleted": machine_id,
        "stopped_agents": [a.id for a in active_agents],
    }


@router.post("/{machine_id}/tokens/regenerate", status_code=200)
async def regenerate_machine_token(
    machine_id: str,
    request: Request,
    revoke_only: bool = False,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Revoke existing tokens, issue a new one, and update the daemon.

    Two modes:

    - **default (push mode)**: Push the new token to the connected daemon
      via a ``rotate_token`` frame so it can persist the token, then
      disconnect. The daemon's reconnect loop will pick up the new token
      automatically and come back online without manual intervention.
    - **``?revoke_only=true`` (incident mode)**: Do NOT push the new token.
      Just revoke the old one and disconnect. Use this when the daemon may
      be compromised — the operator must distribute the new token out-of-band
      and restart the daemon manually.
    """
    await _get_owned_machine(db, machine_id, identity)
    machine_bus = request.app.state.machine_bus

    # Revoke existing tokens
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(MachineToken).where(
            MachineToken.machine_id == machine_id,
            MachineToken.revoked_at.is_(None),
        )
    )
    for tok in result.scalars().all():
        tok.revoked_at = now

    # Issue new token
    plaintext = generate_machine_token()
    hashed, hint = hash_machine_token(plaintext)
    db.add(MachineToken(machine_id=machine_id, token_hash=hashed, lookup_hint=hint))
    await db.commit()

    pushed = False
    if not revoke_only:
        # Try to push the new token to the connected daemon BEFORE disconnecting,
        # so it can persist the token and reconnect automatically.
        pushed = await machine_bus.send(machine_id, {
            "type": "rotate_token",
            "new_token": plaintext,
        })

    # Disconnect the daemon. If push succeeded the daemon now has the new
    # token and will reconnect with it; if push failed (or revoke_only),
    # the operator must update the token file manually.
    disconnected = await machine_bus.disconnect(machine_id)

    return {
        "machine_token": plaintext,
        "pushed_to_daemon": pushed,
        "daemon_disconnected": disconnected,
        "mode": "revoke_only" if revoke_only else "rotate_and_push",
    }


@router.post("/{machine_id}/drain", status_code=200)
async def drain_machine(
    machine_id: str,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Set machine status to ``draining`` (no new agents will be placed)."""
    machine = await _get_owned_machine(db, machine_id, identity)
    machine.status = "draining"
    db.add(MachineActivityLog(machine_id=machine_id, event_type="drain"))
    await db.commit()
    return {"id": machine.id, "status": "draining"}


@router.post("/{machine_id}/tokens/revoke", status_code=200)
async def revoke_machine_token(
    machine_id: str,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Revoke all active tokens for a machine."""
    await _get_owned_machine(db, machine_id, identity)
    result = await db.execute(
        select(MachineToken).where(
            MachineToken.machine_id == machine_id,
            MachineToken.revoked_at.is_(None),
        )
    )
    tokens = result.scalars().all()
    now = datetime.now(timezone.utc)
    for tok in tokens:
        tok.revoked_at = now
    await db.commit()
    return {"revoked": len(tokens)}


# ── Private helpers ──────────────────────────────────────────────────


async def _get_owned_machine(
    db: AsyncSession, machine_id: str, identity: Identity
) -> Machine:
    result = await db.execute(select(Machine).where(Machine.id == machine_id))
    machine = result.scalar_one_or_none()
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    if identity.kind == "user" and identity.claims and identity.claims.is_admin:
        return machine
    if machine.owner_user_id != identity.id:
        raise HTTPException(status_code=403, detail="Not the owner of this machine")
    return machine


# ── Machine detail sub-resources ────────────────────────────────────


class MachineAgentOut(BaseModel):
    id: str
    name: str
    engine: str
    desired_state: str
    actual_state: str
    reasoning_effort: Optional[str] = None
    rooms: list[str] = []
    model_config = {"from_attributes": True}


class MachineEngineOut(BaseModel):
    engine: str
    version: Optional[str] = None


@router.get("/{machine_id}/agents", response_model=list[MachineAgentOut])
async def list_machine_agents(
    machine_id: str,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """List agents placed on a specific machine."""
    from doorae.db.models import Participant, Room

    machine = (await db.execute(select(Machine).where(Machine.id == machine_id))).scalar_one_or_none()
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")

    agents = (await db.execute(
        select(Agent).where(Agent.placed_on_machine_id == machine_id)
    )).scalars().all()

    results = []
    for agent in agents:
        # Get room names for this agent
        room_rows = (await db.execute(
            select(Room.name)
            .join(Participant, Participant.room_id == Room.id)
            .where(Participant.agent_id == agent.id)
        )).scalars().all()

        results.append(MachineAgentOut(
            id=agent.id,
            name=agent.name,
            engine=agent.engine,
            desired_state=agent.desired_state,
            actual_state=agent.actual_state,
            reasoning_effort=agent.reasoning_effort,
            rooms=list(room_rows),
        ))
    return results


@router.get("/{machine_id}/engines", response_model=list[MachineEngineOut])
async def list_machine_engines(
    machine_id: str,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """List engines available on a specific machine."""
    machine = (await db.execute(select(Machine).where(Machine.id == machine_id))).scalar_one_or_none()
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")

    rows = (await db.execute(
        select(MachineEngine).where(MachineEngine.machine_id == machine_id)
    )).scalars().all()

    return [MachineEngineOut(engine=r.engine, version=r.version) for r in rows]


class MachineActivityOut(BaseModel):
    id: str
    machine_id: str
    event_type: str
    timestamp: str
    details: dict | None = None


@router.get("/{machine_id}/activity", response_model=list[MachineActivityOut])
async def get_machine_activity(
    machine_id: str,
    limit: int = 50,
    # Guests are not account holders; machines are a registered-user
    # concern only.
    identity: Identity = Depends(forbid_guest),
    db: AsyncSession = Depends(get_db),
):
    """Return recent activity events for a machine."""
    machine = (await db.execute(select(Machine).where(Machine.id == machine_id))).scalar_one_or_none()
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")

    stmt = (
        select(MachineActivityLog)
        .where(MachineActivityLog.machine_id == machine_id)
        .order_by(MachineActivityLog.timestamp.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        MachineActivityOut(
            id=r.id,
            machine_id=r.machine_id,
            event_type=r.event_type,
            timestamp=r.timestamp.isoformat(),
            details=r.details,
        )
        for r in rows
    ]
