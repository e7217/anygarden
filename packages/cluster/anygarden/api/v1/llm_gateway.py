"""REST endpoints for LLM gateway administration — ``/api/v1/llm-gateway`` (#197).

Admin-only surface that backs the ``AdminLLMGatewayPage`` UI. Three
concerns share the router:

- **Models** (``/models``) — CRUD over ``LLMGatewayModel`` rows plus a
  ``/test`` endpoint that sends a one-token ping through the live
  gateway so admins can verify a newly-registered model before
  relying on it.
- **Secrets** (``/secrets``) — CRUD over ``LLMGatewaySecret`` with
  Fernet-at-rest (shared ``ANYGARDEN_MCP_SECRETS_KEY``, see ADR-004).
  List responses mask the plaintext; PATCH overwrites the stored
  value in place (UI labels this action "Edit").
- **Runtime** (``/status``, ``/apply``, ``/restart``, ``/usage``) —
  inspect and control the supervised LiteLLM subprocess and query
  the usage table the reverse proxy writes to.

``Apply`` is a no-argument POST that respawns litellm with the
current DB state — the "draft → apply" split from §12.3 of the design
doc lives entirely in the DB (the admin's edits land in model/secret
rows as they happen; the child process keeps running the previous
config until Apply is pressed).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import LLMGatewayModel, LLMGatewaySecret, LLMGatewayUsage
from anygarden.dependencies import get_admin_identity, get_db


router = APIRouter(
    prefix="/api/v1/llm-gateway", tags=["llm-gateway-admin"]
)


# Local/self-hosted providers don't require caller-supplied credentials —
# Ollama and most vLLM deployments don't check ``Authorization``. The
# admin UI therefore lets the operator leave ``api_key_ref`` blank for
# these providers; the handler normalises blank input to a fixed
# sentinel so the yaml/env machinery stays uniform. Supervisor
# (``bootstrap.py::_build_spawn_params_factory``) injects the matching
# env var (``ANYGARDEN_LITELLM_OLLAMA_DUMMY=sk-local``) on every spawn.
_LOCAL_PROVIDERS = frozenset({"ollama", "vllm", "custom"})
_OLLAMA_DUMMY_REF = "OLLAMA_DUMMY"


# ── Helpers ────────────────────────────────────────────────────────────


def _get_supervisor_or_503(request: Request) -> Any:
    sup = getattr(request.app.state, "llm_gateway_supervisor", None)
    if sup is None:
        raise HTTPException(
            status_code=503,
            detail="LLM gateway is not enabled",
        )
    return sup


def _get_upstream_or_503(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "llm_gateway_client", None)
    if client is None:
        raise HTTPException(
            status_code=503, detail="LLM gateway is not enabled"
        )
    return client


def _get_gateway_secrets(request: Request) -> Any:
    """Return the Fernet wrapper used to encrypt/decrypt gateway secrets.

    Reused from ``mcp_template_service`` per ADR-004 so the operator
    only has one KMS key to rotate.
    """
    svc = getattr(request.app.state, "mcp_template_service", None)
    if svc is None or getattr(svc, "_secrets", None) is None:
        raise HTTPException(
            status_code=503,
            detail="MCP secrets service is not wired — cannot manage gateway secrets",
        )
    return svc._secrets


def _mask_secret(value: str) -> str:
    """Render a non-reversible preview of a plaintext key.

    ``sk-ant-api03-AbCdEfGhIjKl`` → ``sk-ant-api03-…IjKl``. Keeps the
    provider prefix + last 4 chars, hides the middle. Shorter strings
    fall back to full-mask so we never accidentally expose a whole
    dev token that happens to be 8 chars long.
    """
    if not value or len(value) < 12:
        return "***"
    return f"{value[:12]}…{value[-4:]}"


# ── Schemas — Models ───────────────────────────────────────────────────


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(..., min_length=1, max_length=32)
    upstream_model: str = Field(..., min_length=1, max_length=255)
    # ``api_key_ref`` is optional at the schema layer. The create
    # handler below fills in ``_OLLAMA_DUMMY_REF`` for local providers
    # or rejects blank input for cloud providers, keeping DB rows
    # well-formed without forcing the admin UI to invent a throwaway
    # secret for every Ollama model.
    api_key_ref: Optional[str] = Field(default=None, max_length=64)
    extra_params: Optional[dict] = None
    enabled: bool = True


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    provider: Optional[str] = Field(default=None, min_length=1, max_length=32)
    upstream_model: Optional[str] = Field(default=None, min_length=1, max_length=255)
    # ``max_length=64`` only — empty string allowed so the admin can
    # clear out a stale cloud-provider ref when switching a model to a
    # local provider. Handler normalises/validates same as Create.
    api_key_ref: Optional[str] = Field(default=None, max_length=64)
    extra_params: Optional[dict] = None
    enabled: Optional[bool] = None


class ModelOut(BaseModel):
    id: str
    model_name: str
    provider: str
    upstream_model: str
    api_key_ref: str
    extra_params: Optional[dict]
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Schemas — Secrets ─────────────────────────────────────────────────


class SecretCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # #471 — this name is interpolated into the gateway child process'
    # environment, so it must match POSIX env-var naming (leading letter
    # or underscore, then alphanumerics/underscores). Rejects hyphens,
    # leading digits and other shell-unsafe shapes with a 422.
    env_var_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    value: str = Field(..., min_length=1)


class SecretUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(..., min_length=1)


class SecretOut(BaseModel):
    """Never carries plaintext. ``value_preview`` is a non-reversible hint."""

    env_var_name: str
    value_preview: str
    last_tested_at: Optional[datetime]
    last_test_status: Optional[str]
    created_at: datetime
    updated_at: datetime


# ── Schemas — Runtime ─────────────────────────────────────────────────


class StatusOut(BaseModel):
    state: str
    pid: Optional[int] = None
    port: Optional[int] = None
    crash_count: int = 0
    last_error: Optional[str] = None
    config_hash: Optional[str] = None


class TestResult(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    duration_ms: int
    error: Optional[str] = None


class OllamaModelsRequest(BaseModel):
    # Empty/omitted falls back to Ollama's default loopback endpoint.
    api_base: Optional[str] = None


class OllamaModelsResult(BaseModel):
    # ``ok=false`` rides on a 200 (like TestResult) so the dialog can show
    # a "couldn't reach Ollama" message inline instead of throwing — a
    # failed probe is a normal outcome, not a server error.
    ok: bool
    models: list[str] = []
    error: Optional[str] = None


class UsageBucket(BaseModel):
    key: str
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    # #461 (Wave 2d) — summed USD cost for the bucket. Nullable-safe at
    # the SQL layer (``coalesce(sum(cost_usd), 0)``); rows with no cost
    # signal (gateway-routed openhands, codex/gemini) contribute 0.
    cost_usd: float = 0.0


class UsageOut(BaseModel):
    window_hours: int
    total_requests: int
    # #461 — grand-total USD cost across the window (sum of self-reported
    # per-request costs; an estimate, dominated by claude-code).
    total_cost_usd: float = 0.0
    by_model: list[UsageBucket]
    by_agent: list[UsageBucket]


# ── Models CRUD ───────────────────────────────────────────────────────


@router.get("/models", response_model=list[ModelOut])
async def list_models(
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> list[LLMGatewayModel]:
    rows = (
        await db.execute(
            select(LLMGatewayModel).order_by(LLMGatewayModel.model_name)
        )
    ).scalars().all()
    return list(rows)


def _normalise_api_key_ref(
    provider: str, api_key_ref: Optional[str]
) -> str:
    """Resolve a possibly-blank ``api_key_ref`` against the provider.

    - Local providers (ollama / vllm / custom) tolerate empty input
      and are stored under the shared ``OLLAMA_DUMMY`` sentinel.
    - Cloud providers still require a non-empty reference; raising
      a 422 keeps accidental credential-less Anthropic/OpenAI rows
      out of the DB where they'd silently render an invalid
      ``os.environ/ANYGARDEN_LITELLM_`` reference into the yaml.
    """
    ref = (api_key_ref or "").strip()
    if ref:
        return ref
    if provider in _LOCAL_PROVIDERS:
        return _OLLAMA_DUMMY_REF
    raise HTTPException(
        status_code=422,
        detail=(
            f"provider '{provider}' requires a non-empty api_key_ref"
        ),
    )


@router.post(
    "/models",
    response_model=ModelOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    body: ModelCreate,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> LLMGatewayModel:
    existing = (
        await db.execute(
            select(LLMGatewayModel).where(
                LLMGatewayModel.model_name == body.model_name
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Model '{body.model_name}' already exists",
        )

    payload = body.model_dump()
    payload["api_key_ref"] = _normalise_api_key_ref(
        body.provider, body.api_key_ref
    )
    row = LLMGatewayModel(**payload)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.patch("/models/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: str,
    body: ModelUpdate,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> LLMGatewayModel:
    row = await db.get(LLMGatewayModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")

    updates = body.model_dump(exclude_unset=True)
    if "model_name" in updates and updates["model_name"] != row.model_name:
        conflict = (
            await db.execute(
                select(LLMGatewayModel).where(
                    LLMGatewayModel.model_name == updates["model_name"],
                    LLMGatewayModel.id != model_id,
                )
            )
        ).scalar_one_or_none()
        if conflict is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Model name '{updates['model_name']}' already in use",
            )

    # If the PATCH touches ``api_key_ref``, validate against the final
    # provider (new one if also in this PATCH, otherwise the row's
    # current one). Blank input under a local provider becomes the
    # shared sentinel; blank input under a cloud provider is a 422.
    if "api_key_ref" in updates:
        final_provider = updates.get("provider", row.provider)
        updates["api_key_ref"] = _normalise_api_key_ref(
            final_provider, updates["api_key_ref"]
        )

    for k, v in updates.items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: str,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await db.get(LLMGatewayModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")
    await db.delete(row)
    await db.commit()


@router.post("/models/{model_id}/test", response_model=TestResult)
async def test_model(
    model_id: str,
    request: Request,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> TestResult:
    """Send a one-token ping through the gateway with this model.

    Exercises the full live path (litellm subprocess + upstream
    provider) so an admin knows whether a newly-added model actually
    works before agents start using it.
    """
    row = await db.get(LLMGatewayModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")

    supervisor = _get_supervisor_or_503(request)
    client = _get_upstream_or_503(request)
    master_key = supervisor.master_key
    if master_key is None:
        raise HTTPException(
            status_code=503, detail="Gateway not running — cannot test"
        )

    # Use Anthropic /v1/messages for any Anthropic-shaped model,
    # OpenAI /v1/chat/completions for the rest. LiteLLM accepts both
    # paths on the same port and routes by config.yaml.
    if row.provider == "anthropic" or row.upstream_model.startswith("anthropic/"):
        url = "/v1/messages"
        payload = {
            "model": row.model_name,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
    else:
        url = "/v1/chat/completions"
        payload = {
            "model": row.model_name,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }

    headers = {
        "Authorization": f"Bearer {master_key}",
        "Content-Type": "application/json",
    }
    start = time.perf_counter()
    try:
        resp = await client.post(url, headers=headers, json=payload, timeout=10.0)
    except httpx.HTTPError as exc:
        return TestResult(
            ok=False,
            duration_ms=int((time.perf_counter() - start) * 1000),
            error=f"{exc!r}"[:256],
        )

    duration_ms = int((time.perf_counter() - start) * 1000)
    ok = 200 <= resp.status_code < 300
    err = None if ok else resp.text[:256]
    return TestResult(
        ok=ok, status_code=resp.status_code, duration_ms=duration_ms, error=err
    )


_OLLAMA_DEFAULT_BASE = "http://localhost:11434"


@router.post("/ollama/models", response_model=OllamaModelsResult)
async def list_ollama_models(
    body: OllamaModelsRequest,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
) -> OllamaModelsResult:
    """List models installed on an Ollama instance via ``GET /api/tags``.

    Backend-proxied (not called from the browser) so it works when
    ``api_base`` is a server-internal address and to dodge Ollama's
    default CORS policy. Independent of the gateway supervisor — model
    registration happens before Apply, so this must work even when
    litellm isn't running.
    """
    base = (body.api_base or "").strip() or _OLLAMA_DEFAULT_BASE
    url = base.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        return OllamaModelsResult(ok=False, error=f"{exc!r}"[:256])
    if resp.status_code != 200:
        return OllamaModelsResult(
            ok=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        return OllamaModelsResult(ok=False, error=f"invalid JSON from Ollama: {exc!r}"[:256])
    names = [
        m["name"]
        for m in data.get("models", [])
        if isinstance(m, dict) and m.get("name")
    ]
    return OllamaModelsResult(ok=True, models=names)


# ── Secrets CRUD ──────────────────────────────────────────────────────


@router.get("/secrets", response_model=list[SecretOut])
async def list_secrets(
    request: Request,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> list[SecretOut]:
    rows = (
        await db.execute(
            select(LLMGatewaySecret).order_by(LLMGatewaySecret.env_var_name)
        )
    ).scalars().all()

    secrets_svc = _get_gateway_secrets(request)
    out: list[SecretOut] = []
    for row in rows:
        try:
            payload = secrets_svc.decrypt_dict(row.encrypted_value)
            plaintext = payload.get("v", "")
        except Exception:  # noqa: BLE001
            plaintext = ""
        out.append(
            SecretOut(
                env_var_name=row.env_var_name,
                value_preview=_mask_secret(plaintext),
                last_tested_at=row.last_tested_at,
                last_test_status=row.last_test_status,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return out


@router.post(
    "/secrets", response_model=SecretOut, status_code=status.HTTP_201_CREATED
)
async def create_secret(
    body: SecretCreate,
    request: Request,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> SecretOut:
    existing = await db.get(LLMGatewaySecret, body.env_var_name)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Secret '{body.env_var_name}' already exists — use PATCH to update the value",
        )

    secrets_svc = _get_gateway_secrets(request)
    ciphertext = secrets_svc.encrypt_dict({"v": body.value})
    row = LLMGatewaySecret(
        env_var_name=body.env_var_name, encrypted_value=ciphertext
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return SecretOut(
        env_var_name=row.env_var_name,
        value_preview=_mask_secret(body.value),
        last_tested_at=None,
        last_test_status=None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.patch("/secrets/{env_var_name}", response_model=SecretOut)
async def update_secret(
    env_var_name: str,
    body: SecretUpdate,
    request: Request,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> SecretOut:
    """Overwrite the stored value for ``env_var_name``.

    The UI surfaces this as an "Edit" action. Any previous test
    result is invalidated since the new value has not been verified
    against the upstream provider yet.
    """
    row = await db.get(LLMGatewaySecret, env_var_name)
    if row is None:
        raise HTTPException(status_code=404, detail="Secret not found")

    secrets_svc = _get_gateway_secrets(request)
    row.encrypted_value = secrets_svc.encrypt_dict({"v": body.value})
    # The previous test result described a different value — drop it
    # so the UI doesn't show a stale green "Valid" badge against a
    # key that has not actually been tried.
    row.last_tested_at = None
    row.last_test_status = None
    await db.commit()
    await db.refresh(row)
    return SecretOut(
        env_var_name=row.env_var_name,
        value_preview=_mask_secret(body.value),
        last_tested_at=None,
        last_test_status=None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.delete(
    "/secrets/{env_var_name}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_secret(
    env_var_name: str,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await db.get(LLMGatewaySecret, env_var_name)
    if row is None:
        raise HTTPException(status_code=404, detail="Secret not found")
    await db.delete(row)
    await db.commit()


# ── Runtime control ──────────────────────────────────────────────────


@router.get("/status", response_model=StatusOut)
async def get_status(
    request: Request,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
) -> StatusOut:
    supervisor = _get_supervisor_or_503(request)
    snapshot = supervisor.status()
    state_value = (
        snapshot.state.value
        if hasattr(snapshot.state, "value")
        else str(snapshot.state)
    )
    return StatusOut(
        state=state_value,
        pid=snapshot.pid,
        port=snapshot.port,
        crash_count=snapshot.crash_count,
        last_error=snapshot.last_error,
        config_hash=snapshot.config_hash,
    )


@router.post("/apply", response_model=StatusOut)
async def apply_config(
    request: Request,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
) -> StatusOut:
    """Respawn litellm with the current DB state.

    The admin's draft changes (model/secret rows) are already in the
    DB by the time they click Apply — this endpoint just kicks the
    supervisor so the child process picks them up on its next spawn.
    """
    supervisor = _get_supervisor_or_503(request)
    await supervisor.restart()
    snapshot = supervisor.status()
    state_value = (
        snapshot.state.value
        if hasattr(snapshot.state, "value")
        else str(snapshot.state)
    )
    return StatusOut(
        state=state_value,
        pid=snapshot.pid,
        port=snapshot.port,
        crash_count=snapshot.crash_count,
        last_error=snapshot.last_error,
        config_hash=snapshot.config_hash,
    )


@router.post("/restart", response_model=StatusOut)
async def restart_gateway(
    request: Request,
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
) -> StatusOut:
    """Hard restart button — same mechanism as ``/apply`` today.

    Kept distinct so the UI can surface it as a recovery action
    (FAILED → admin presses Restart) separate from an ordinary
    config change. Both paths go through ``supervisor.restart()``.
    """
    return await apply_config(request)  # type: ignore[arg-type]


# ── Usage ─────────────────────────────────────────────────────────────


@router.get("/usage", response_model=UsageOut)
async def get_usage(
    window: str = "24h",
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> UsageOut:
    """Aggregate usage counters from ``LLMGatewayUsage`` within ``window``.

    ``window`` accepts ``Nh`` / ``Nd`` (hours / days). Out-of-range or
    unparseable values fall back to 24h rather than 400 — admin UI
    trusts server defaults.
    """
    hours = _parse_window(window)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    total = (
        await db.execute(
            select(func.count(LLMGatewayUsage.id)).where(
                LLMGatewayUsage.timestamp >= since
            )
        )
    ).scalar_one()

    # #461 — grand-total USD cost (nullable-safe). Rows with no cost
    # signal contribute 0 via coalesce.
    total_cost = (
        await db.execute(
            select(
                func.coalesce(func.sum(LLMGatewayUsage.cost_usd), 0.0)
            ).where(LLMGatewayUsage.timestamp >= since)
        )
    ).scalar_one()

    by_model_rows = (
        await db.execute(
            select(
                LLMGatewayUsage.model_name,
                func.count(LLMGatewayUsage.id),
                func.coalesce(func.sum(LLMGatewayUsage.prompt_tokens), 0),
                func.coalesce(func.sum(LLMGatewayUsage.completion_tokens), 0),
                # #461 — nullable-safe USD cost sum per model.
                func.coalesce(func.sum(LLMGatewayUsage.cost_usd), 0.0),
            )
            .where(LLMGatewayUsage.timestamp >= since)
            .group_by(LLMGatewayUsage.model_name)
            .order_by(func.count(LLMGatewayUsage.id).desc())
        )
    ).all()

    by_agent_rows = (
        await db.execute(
            select(
                LLMGatewayUsage.agent_id,
                func.count(LLMGatewayUsage.id),
                func.coalesce(func.sum(LLMGatewayUsage.prompt_tokens), 0),
                func.coalesce(func.sum(LLMGatewayUsage.completion_tokens), 0),
                # #461 — nullable-safe USD cost sum per agent.
                func.coalesce(func.sum(LLMGatewayUsage.cost_usd), 0.0),
            )
            .where(
                LLMGatewayUsage.timestamp >= since,
                LLMGatewayUsage.agent_id.is_not(None),
            )
            .group_by(LLMGatewayUsage.agent_id)
            .order_by(func.count(LLMGatewayUsage.id).desc())
            .limit(50)
        )
    ).all()

    return UsageOut(
        window_hours=hours,
        total_requests=int(total or 0),
        total_cost_usd=float(total_cost or 0.0),
        by_model=[
            UsageBucket(
                key=name, request_count=int(cnt),
                prompt_tokens=int(pt), completion_tokens=int(ct),
                cost_usd=float(cost or 0.0),
            )
            for (name, cnt, pt, ct, cost) in by_model_rows
        ],
        by_agent=[
            UsageBucket(
                key=str(agent_id), request_count=int(cnt),
                prompt_tokens=int(pt), completion_tokens=int(ct),
                cost_usd=float(cost or 0.0),
            )
            for (agent_id, cnt, pt, ct, cost) in by_agent_rows
        ],
    )


def _parse_window(value: str) -> int:
    """Return window size in hours. Fallback: 24h."""
    value = (value or "").strip().lower()
    if not value:
        return 24
    try:
        if value.endswith("h"):
            n = int(value[:-1])
            return max(1, min(n, 24 * 30))
        if value.endswith("d"):
            n = int(value[:-1])
            return max(1, min(n * 24, 24 * 30))
        n = int(value)
        return max(1, min(n, 24 * 30))
    except ValueError:
        return 24
