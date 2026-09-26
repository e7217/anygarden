"""Admin-only local endpoint configuration. Secret values are write-only."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, EngineCredential, Machine
from anygarden.dependencies import get_admin_identity, get_db
from anygarden.engines.endpoints import (
    EndpointConfiguration,
    ModelProbeError,
    credential_for,
    probe_models,
    validate_base_url,
)
from anygarden.engines.validation import pi_provider_error
from anygarden.scheduler.placement import NoSuitableMachineError, select_machine_for

router = APIRouter(prefix="/api/v1/agents", tags=["agent-endpoints"])
probe_router = APIRouter(prefix="/api/v1/engine-endpoints", tags=["agent-endpoints"])


async def _agent(db, agent_id):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


def _secrets(request):
    service = getattr(request.app.state, "mcp_template_service", None)
    secrets = getattr(service, "_secrets", None)
    if secrets is None:
        raise HTTPException(503, "Credential encryption service is unavailable")
    return secrets


async def _body(request):
    # Manually validate secret-bearing input so FastAPI/Pydantic never echoes
    # credential values (including malformed URL values) in error responses.
    raw = await request.body()
    if len(raw) > 16384:
        raise HTTPException(422, "Configuration payload is too large")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise TypeError
        return body
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid configuration payload") from None


def _credential_out(row):
    return {
        "id": row.id,
        "label": row.label,
        "engine": row.engine,
        "revision": row.revision,
        "stored": True,
    }


def _out(agent):
    return {
        "provider": agent.provider,
        "model": agent.model,
        "base_url": agent.base_url,
        "api_protocol": agent.api_protocol,
        "credential_ref": agent.credential_ref,
    }


@router.get("/{agent_id}/endpoint")
async def get_endpoint(
    agent_id: str,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return _out(await _agent(db, agent_id))


@router.put("/{agent_id}/endpoint")
async def put_endpoint(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await _agent(db, agent_id)
    body = await _body(request)
    native_switch = (
        isinstance(body, dict)
        and set(body) == {"base_url", "provider", "model"}
        and body.get("base_url") is None
    )
    codex_reset = body == {"base_url": None, "model": None}
    if body == {"base_url": None} or native_switch or codex_reset:
        if codex_reset and agent.engine != "codex-cli":
            raise HTTPException(422, "Only Codex can reset to its default model")
        if native_switch:
            provider, model = body["provider"], body["model"]
            if (
                agent.engine != "pi-cli"
                or pi_provider_error(agent.engine, provider)
                or not isinstance(model, str)
                or not (1 <= len(model.strip()) <= 256)
            ):
                raise HTTPException(422, "Invalid Pi native provider or model")
            model = model.strip()
        changed = any((agent.base_url, agent.api_protocol, agent.credential_ref))
        if native_switch:
            changed = changed or agent.provider != provider or agent.model != model
            agent.provider, agent.model = provider, model
        if codex_reset:
            changed = changed or agent.provider is not None or agent.model is not None
            agent.provider = agent.model = None
        agent.base_url = agent.api_protocol = agent.credential_ref = None
    else:
        try:
            config = EndpointConfiguration.model_validate(body)
            config.validate_engine(agent.engine)
            if config.credential_ref:
                await credential_for(db, agent, config.credential_ref)
        except ValueError:
            raise HTTPException(
                422,
                "Invalid endpoint configuration or credential reference for this agent",
            ) from None
        try:
            bus = request.app.state.machine_bus
            if agent.placed_on_machine_id:
                machine = await db.get(Machine, agent.placed_on_machine_id)
                if (
                    machine is None
                    or machine.status != "online"
                    or not bus.is_connected(machine.id)
                    or "direct_endpoint_v1" not in (machine.control_capabilities or [])
                ):
                    raise NoSuitableMachineError(
                        "Placed machine cannot enforce direct endpoints"
                    )
            else:
                await select_machine_for(
                    agent.engine,
                    db,
                    bus,
                    required_control_capabilities={"direct_endpoint_v1"},
                )
        except NoSuitableMachineError:
            raise HTTPException(
                409,
                "No available machine supports direct endpoints; update or connect a machine with direct_endpoint_v1 capability",
            ) from None
        values = config.model_dump()
        changed = any(getattr(agent, key) != value for key, value in values.items())
        for key, value in values.items():
            setattr(agent, key, value)
    await db.commit()
    if changed:
        await request.app.state.agent_lifecycle.bump_generation(agent.id)
    return _out(agent)


@router.get("/{agent_id}/endpoint/credentials")
async def list_credentials(
    agent_id: str,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await _agent(db, agent_id)
    rows = (
        (
            await db.execute(
                select(EngineCredential).where(
                    EngineCredential.agent_id == agent.id,
                    EngineCredential.engine == agent.engine,
                )
            )
        )
        .scalars()
        .all()
    )
    return [_credential_out(row) for row in rows]


def _secret_value(body):
    if set(body) - {"label", "value"}:
        raise HTTPException(422, "Invalid credential payload")
    value = body.get("value")
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 8192
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise HTTPException(422, "Credential must be a nonempty printable token")
    label = body.get("label", "Endpoint credential")
    if not isinstance(label, str) or not label or len(label) > 128:
        raise HTTPException(422, "Invalid credential label")
    return value, label


@router.post("/{agent_id}/endpoint/credentials", status_code=201)
async def create_credential(
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await _agent(db, agent_id)
    if agent.engine not in {"codex-cli", "pi-cli"}:
        raise HTTPException(422, "Direct endpoints require Codex or Pi")
    value, label = _secret_value(await _body(request))
    row = EngineCredential(
        agent_id=agent.id,
        engine=agent.engine,
        label=label,
        encrypted_value=_secrets(request).encrypt_dict({"v": value}),
    )
    db.add(row)
    await db.commit()
    return _credential_out(row)


@router.put("/{agent_id}/endpoint/credentials/{credential_id}")
async def rotate_credential(
    agent_id: str,
    credential_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await _agent(db, agent_id)
    try:
        row = await credential_for(db, agent, credential_id)
    except ValueError:
        raise HTTPException(
            404, "Credential not found for this agent and engine"
        ) from None
    value, label = _secret_value(await _body(request))
    await db.execute(
        update(EngineCredential)
        .where(EngineCredential.id == row.id)
        .values(
            encrypted_value=_secrets(request).encrypt_dict({"v": value}),
            label=label,
            revision=EngineCredential.revision + 1,
        )
    )
    await db.commit()
    await db.refresh(row)
    if agent.credential_ref == row.id:
        await request.app.state.agent_lifecycle.bump_generation(agent.id)
    return _credential_out(row)


@router.delete("/{agent_id}/endpoint/credentials/{credential_id}", status_code=204)
async def delete_credential(
    agent_id: str,
    credential_id: str,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await _agent(db, agent_id)
    row = await db.get(EngineCredential, credential_id)
    if row is None or row.agent_id != agent.id:
        raise HTTPException(404, "Credential not found for this agent")
    if agent.credential_ref == row.id:
        raise HTTPException(
            409, "Credential is in use; disable or change the endpoint first"
        )
    await db.delete(row)
    await db.commit()


_PROBE_FIELDS = {"base_url", "api_key", "agent_id", "credential_ref"}


@probe_router.post("/models")
async def discover_models(
    request: Request,
    identity: Annotated[Identity, Depends(get_admin_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List model IDs served at ``{base_url}/models`` (#685).

    Probed from this server, so the result is labelled
    ``reachable_from: "server"`` — the placed machine may still differ.
    Accepts either a not-yet-stored ``api_key`` or a stored credential of
    the given agent; neither is ever echoed.
    """
    body = await _body(request)
    if set(body) - _PROBE_FIELDS:
        raise HTTPException(422, "Invalid probe payload")
    try:
        base_url = validate_base_url(body.get("base_url"))
    except ValueError:
        raise HTTPException(
            422, "Endpoint URL must be HTTP(S), without credentials, query or fragment"
        ) from None
    api_key = body.get("api_key")
    if api_key is not None:
        api_key, _ = _secret_value({"value": api_key})
    ref = body.get("credential_ref")
    if ref is not None:
        if api_key is not None or not isinstance(body.get("agent_id"), str):
            raise HTTPException(422, "Invalid probe payload")
        agent = await _agent(db, body["agent_id"])
        try:
            row = await credential_for(db, agent, ref)
            api_key = _secrets(request).decrypt_dict(row.encrypted_value)["v"]
        except (ValueError, TypeError, KeyError):
            raise HTTPException(
                422, "Credential reference is unavailable for this agent"
            ) from None
    try:
        models = await probe_models(
            base_url,
            api_key,
            transport=getattr(request.app.state, "engine_probe_transport", None),
        )
    except ModelProbeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    return {
        "models": [
            {"id": m.id, "max_model_len": m.max_model_len} for m in models
        ],
        "reachable_from": "server",
    }
