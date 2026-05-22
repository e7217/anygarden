"""REST endpoints for MCP server templates + per-agent instances (#124).

Two logical surfaces live under this router:

- ``/api/v1/admin/mcp-templates`` — the catalog itself. List / create
  / update / delete custom templates; builtin rows are read-only.
- ``/api/v1/admin/agents/{agent_id}/mcp-instances`` — per-agent
  attachments. Attach (upsert), detach, toggle enabled.

All endpoints require admin identity. Mutating endpoints bump the
target agent's generation when (and only when) the change would
actually affect the next spawn frame — matches the skills_library
bump-gating contract so admins don't get gratuitous respawns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import (
    Agent,
    MCPServerInstance,
    MCPServerTemplate,
)
from anygarden.dependencies import get_admin_identity, get_db
from anygarden.mcp_templates.service import (
    EngineIncompatible,
    InvalidTemplateConfig,
    MCPTemplateService,
    MissingRequiredEnv,
    TemplateImmutable,
    TemplateInUse,
    TemplateNameConflict,
    TemplateNotFound,
)


router = APIRouter(tags=["mcp-templates"])


# ── Schemas ────────────────────────────────────────────────────────


class MCPTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    icon: Optional[str] = None
    config_per_engine: dict[str, dict] = Field(default_factory=dict)
    required_env_vars: list[str] = Field(default_factory=list)
    supported_engines: list[str] = Field(default_factory=list)


class MCPTemplateUpdate(BaseModel):
    """All fields optional — partial updates. ``name`` is intentionally
    not editable because the name is the join key to instances and
    the builtin seed; renaming silently would orphan attached rows."""

    display_name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    config_per_engine: Optional[dict[str, dict]] = None
    required_env_vars: Optional[list[str]] = None
    supported_engines: Optional[list[str]] = None


class MCPTemplateOut(BaseModel):
    id: str
    name: str
    display_name: str
    description: Optional[str]
    icon: Optional[str]
    config_per_engine: dict[str, dict]
    required_env_vars: list[str]
    supported_engines: list[str]
    source: str
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime
    instance_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class MCPInstanceAttach(BaseModel):
    template_id: str
    env_values: dict[str, str] = Field(default_factory=dict)


class MCPInstancePatch(BaseModel):
    enabled: bool


class MCPInstanceOut(BaseModel):
    id: str
    template_id: str
    template_name: str
    agent_id: str
    enabled: bool
    # We deliberately do NOT echo the decrypted env_values in list
    # responses — the whole point of encryption is that the server
    # hands them over only to the engine. The UI shows "credentials
    # set" as a boolean instead.
    has_credentials: bool
    required_env_vars: list[str]
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Helpers ────────────────────────────────────────────────────────


def _service(request: Request) -> MCPTemplateService:
    service = getattr(request.app.state, "mcp_template_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="mcp_template_service not configured on app.state",
        )
    return service


def _lifecycle(request: Request):
    return request.app.state.agent_lifecycle


async def _template_with_count(
    db: AsyncSession, row: MCPServerTemplate,
) -> MCPTemplateOut:
    # Single cheap count keeps the admin catalog page showing
    # "N agents attached" without an N+1 query per row.
    from sqlalchemy import func

    count = (
        await db.execute(
            select(func.count(MCPServerInstance.id)).where(
                MCPServerInstance.template_id == row.id
            )
        )
    ).scalar_one()
    return MCPTemplateOut(
        id=row.id,
        name=row.name,
        display_name=row.display_name,
        description=row.description,
        icon=row.icon,
        config_per_engine=row.config_per_engine or {},
        required_env_vars=list(row.required_env_vars or []),
        supported_engines=list(row.supported_engines or []),
        source=row.source,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        instance_count=count,
    )


# ── Template CRUD ─────────────────────────────────────────────────


@router.get("/api/v1/admin/mcp-templates", response_model=list[MCPTemplateOut])
async def list_templates(
    source: Optional[str] = None,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
    request: Request = None,  # type: ignore[assignment]
) -> list[MCPTemplateOut]:
    service = _service(request)
    # ``source`` query param narrows to builtin or custom; None =
    # return everything so the UI can show both tabs without two
    # requests.
    if source not in (None, "builtin", "custom"):
        raise HTTPException(400, detail="source must be 'builtin' or 'custom'")
    rows = await service.list_templates(db, source=source)
    return [await _template_with_count(db, row) for row in rows]


@router.post(
    "/api/v1/admin/mcp-templates",
    status_code=201,
    response_model=MCPTemplateOut,
)
async def create_template(
    body: MCPTemplateCreate,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> MCPTemplateOut:
    service = _service(request)
    try:
        row = await service.create_custom(
            db,
            name=body.name,
            display_name=body.display_name,
            description=body.description,
            icon=body.icon,
            config_per_engine=body.config_per_engine,
            required_env_vars=body.required_env_vars,
            supported_engines=body.supported_engines,
            created_by=identity.id,
        )
    except TemplateNameConflict:
        raise HTTPException(409, detail=f"Template '{body.name}' already exists")
    except InvalidTemplateConfig as exc:
        raise HTTPException(422, detail=str(exc))
    return await _template_with_count(db, row)


@router.put(
    "/api/v1/admin/mcp-templates/{template_id}",
    response_model=MCPTemplateOut,
)
async def update_template(
    template_id: str,
    body: MCPTemplateUpdate,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> MCPTemplateOut:
    service = _service(request)
    try:
        row = await service.update_custom(
            db,
            template_id,
            display_name=body.display_name,
            description=body.description,
            icon=body.icon,
            config_per_engine=body.config_per_engine,
            required_env_vars=body.required_env_vars,
            supported_engines=body.supported_engines,
        )
    except TemplateNotFound:
        raise HTTPException(404, detail="Template not found")
    except TemplateImmutable:
        raise HTTPException(403, detail="Builtin templates are read-only")
    except InvalidTemplateConfig as exc:
        raise HTTPException(422, detail=str(exc))

    # An edit to a template in active use should re-materialise
    # every agent that has it attached, because the overlay changed.
    await _bump_attached_agents(request, db, template_id)
    return await _template_with_count(db, row)


@router.delete("/api/v1/admin/mcp-templates/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _service(request)
    try:
        await service.delete_custom(db, template_id)
    except TemplateNotFound:
        raise HTTPException(404, detail="Template not found")
    except TemplateImmutable:
        raise HTTPException(403, detail="Builtin templates are read-only")
    except TemplateInUse:
        raise HTTPException(
            409,
            detail="Template is attached to one or more agents; detach first",
        )
    return Response(status_code=204)


# ── Per-agent instance CRUD ──────────────────────────────────────


@router.get(
    "/api/v1/admin/agents/{agent_id}/mcp-instances",
    response_model=list[MCPInstanceOut],
)
async def list_instances(
    agent_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> list[MCPInstanceOut]:
    service = _service(request)
    pairs = await service.list_instances_for_agent(db, agent_id)
    return [
        MCPInstanceOut(
            id=inst.id,
            template_id=inst.template_id,
            template_name=tpl.name,
            agent_id=inst.agent_id,
            enabled=inst.enabled,
            has_credentials=bool(inst.env_values_encrypted),
            required_env_vars=list(tpl.required_env_vars or []),
            created_at=inst.created_at,
            updated_at=inst.updated_at,
        )
        for inst, tpl in pairs
    ]


@router.post(
    "/api/v1/admin/agents/{agent_id}/mcp-instances",
    status_code=201,
    response_model=MCPInstanceOut,
)
async def attach_instance(
    agent_id: str,
    body: MCPInstanceAttach,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> MCPInstanceOut:
    service = _service(request)
    agent = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, detail="Agent not found")

    try:
        result = await service.attach(
            db,
            agent_id=agent_id,
            template_id=body.template_id,
            env_values=body.env_values,
        )
    except TemplateNotFound:
        raise HTTPException(404, detail="Template not found")
    except EngineIncompatible as exc:
        raise HTTPException(409, detail=str(exc))
    except MissingRequiredEnv as exc:
        raise HTTPException(422, detail=str(exc))

    if result.created or result.credentials_changed:
        lifecycle = _lifecycle(request)
        await lifecycle.bump_generation(agent_id)

    template = await service.get_template(db, body.template_id)
    return MCPInstanceOut(
        id=result.instance.id,
        template_id=result.instance.template_id,
        template_name=template.name,
        agent_id=result.instance.agent_id,
        enabled=result.instance.enabled,
        has_credentials=bool(result.instance.env_values_encrypted),
        required_env_vars=list(template.required_env_vars or []),
        created_at=result.instance.created_at,
        updated_at=result.instance.updated_at,
    )


@router.patch(
    "/api/v1/admin/agents/{agent_id}/mcp-instances/{instance_id}",
    response_model=MCPInstanceOut,
)
async def patch_instance(
    agent_id: str,
    instance_id: str,
    body: MCPInstancePatch,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> MCPInstanceOut:
    service = _service(request)
    try:
        changed = await service.set_enabled(
            db,
            agent_id=agent_id,
            instance_id=instance_id,
            enabled=body.enabled,
        )
    except LookupError:
        raise HTTPException(404, detail="Instance not found")

    if changed:
        lifecycle = _lifecycle(request)
        await lifecycle.bump_generation(agent_id)

    instance = (
        await db.execute(
            select(MCPServerInstance).where(MCPServerInstance.id == instance_id)
        )
    ).scalar_one()
    template = await service.get_template(db, instance.template_id)
    return MCPInstanceOut(
        id=instance.id,
        template_id=instance.template_id,
        template_name=template.name,
        agent_id=instance.agent_id,
        enabled=instance.enabled,
        has_credentials=bool(instance.env_values_encrypted),
        required_env_vars=list(template.required_env_vars or []),
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


@router.delete(
    "/api/v1/admin/agents/{agent_id}/mcp-instances/{instance_id}",
    status_code=204,
)
async def detach_instance(
    agent_id: str,
    instance_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _service(request)
    deleted = await service.detach(
        db, agent_id=agent_id, instance_id=instance_id,
    )
    if deleted:
        lifecycle = _lifecycle(request)
        await lifecycle.bump_generation(agent_id)
    return Response(status_code=204)


# ── Internal helpers ─────────────────────────────────────────────


async def _bump_attached_agents(
    request: Request, db: AsyncSession, template_id: str,
) -> None:
    agent_ids = list(
        (
            await db.execute(
                select(MCPServerInstance.agent_id).where(
                    MCPServerInstance.template_id == template_id,
                    MCPServerInstance.enabled.is_(True),
                )
            )
        ).scalars().all()
    )
    lifecycle = _lifecycle(request)
    for agent_id in agent_ids:
        await lifecycle.bump_generation(agent_id)
