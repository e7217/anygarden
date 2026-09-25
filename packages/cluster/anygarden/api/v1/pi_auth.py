"""Write-only administration of isolated native Pi provider credentials."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.api.v1.engine_endpoints import _agent, _body, _secret_value, _secrets
from anygarden.auth.dependencies import Identity
from anygarden.db.models import PiNativeCredential
from anygarden.dependencies import get_admin_identity, get_db
from anygarden.engines.validation import pi_provider_error

router = APIRouter(prefix="/api/v1/agents", tags=["pi-native-auth"])


def _out(agent, row):
    active = row is not None and row.provider == agent.provider
    return {
        "configured": active,
        "provider": row.provider if row else None,
        "revision": row.revision if row else None,
    }


async def _pi_agent(db, agent_id):
    agent = await _agent(db, agent_id)
    if agent.engine != "pi-cli":
        raise HTTPException(422, "Pi authentication is only available for Pi agents")
    return agent


@router.get("/{agent_id}/pi-auth")
async def get_pi_auth(
    agent_id: str,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await _pi_agent(db, agent_id)
    return _out(agent, await db.get(PiNativeCredential, agent.id))


@router.put("/{agent_id}/pi-auth")
async def put_pi_auth(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await _pi_agent(db, agent_id)
    error = pi_provider_error(agent.engine, agent.provider)
    if error:
        raise HTTPException(422, error)
    body = await _body(request)
    if set(body) != {"value"}:
        raise HTTPException(422, "Invalid Pi credential payload")
    value, _ = _secret_value(body)
    row = await db.get(PiNativeCredential, agent.id)
    if row is None:
        row = PiNativeCredential(agent_id=agent.id, provider=agent.provider, revision=1)
        db.add(row)
    else:
        row.provider = agent.provider
        row.revision += 1
    row.encrypted_value = _secrets(request).encrypt_dict({"v": value})
    row.created_at = datetime.now(UTC)
    await db.commit()
    await request.app.state.agent_lifecycle.bump_generation(agent.id)
    return _out(agent, row)


@router.delete("/{agent_id}/pi-auth", status_code=204)
async def delete_pi_auth(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await _pi_agent(db, agent_id)
    row = await db.get(PiNativeCredential, agent.id)
    if row is not None:
        await db.delete(row)
        await db.commit()
        await request.app.state.agent_lifecycle.bump_generation(agent.id)
