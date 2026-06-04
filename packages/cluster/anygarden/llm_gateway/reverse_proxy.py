"""``/api/v1/llm/*`` reverse proxy (#197).

All agent LLM traffic enters anygarden through this router. The handler
responsibilities:

1. Authenticate the caller via the existing identity dependency
   (``auth.dependencies``). Any of user / agent / machine tokens
   pass — an agent must be able to hit this to make its actual LLM
   call, so it is not admin-gated.
2. Replace the caller's ``Authorization`` header with
   ``Bearer <gateway-master-key>`` — the supervisor's ephemeral
   master key, shared in-process with the reverse proxy.
3. Forward the request (method + headers + body or stream) to
   ``http://127.0.0.1:<port>/<path:path>`` via a long-lived
   ``httpx.AsyncClient``.
4. Relay the response as a ``StreamingResponse`` when SSE is
   negotiated, or a regular JSON ``Response`` otherwise.
5. After the response completes, record one row in
   ``llm_gateway_usage`` via a background task so request latency
   doesn't pay for the DB round-trip.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import LLMGatewayUsage
from anygarden.dependencies import get_current_identity, get_db
from anygarden.llm_gateway.usage_logger import parse_json_usage, parse_stream_event

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/llm", tags=["llm-gateway-proxy"])


# ── Dependencies ──────────────────────────────────────────────────────


def get_upstream_client(request: Request) -> httpx.AsyncClient:
    """Return the shared httpx client that talks to the LiteLLM subprocess.

    Set on ``app.state.llm_gateway_client`` during lifespan. Raises 503
    if the gateway isn't wired up (feature flag off).
    """
    client: httpx.AsyncClient | None = getattr(
        request.app.state, "llm_gateway_client", None
    )
    if client is None:
        raise HTTPException(status_code=503, detail="LLM gateway is not enabled")
    return client


def get_supervisor(request: Request) -> Any:
    """Return the :class:`LLMGatewaySupervisor`. 503 if not wired."""
    sup = getattr(request.app.state, "llm_gateway_supervisor", None)
    if sup is None:
        raise HTTPException(status_code=503, detail="LLM gateway is not enabled")
    return sup


# ── Usage logging ─────────────────────────────────────────────────────


async def _write_usage_row(
    session_factory: Any,
    *,
    identity_kind: str,
    identity_id: str,
    agent_id: str | None,
    model_name: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    duration_ms: int,
    status_code: int,
    error: str | None = None,
    room_id: str | None = None,
) -> None:
    """Persist one usage row. Called from a FastAPI BackgroundTask.

    Swallows exceptions so a DB hiccup can't poison the caller — the
    proxy has already responded by the time this runs. ``room_id`` is
    filled from the tracing correlation (#420) when the call could be
    tied to a single in-flight request; it stays ``None`` otherwise.
    """
    try:
        async with session_factory() as db:
            db.add(
                LLMGatewayUsage(
                    identity_kind=identity_kind,
                    identity_id=identity_id,
                    agent_id=agent_id,
                    room_id=room_id,
                    model_name=model_name or "",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    duration_ms=duration_ms,
                    status_code=status_code,
                    error=error,
                )
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_gateway.usage_write_failed", error=str(exc))


def _parse_sse_chunk_for_usage(buffer: bytes) -> Any | None:
    """Extract a :class:`ParsedUsage` from any complete SSE event in the buffer.

    Walks the ``data: {...}`` JSON lines and returns the last non-None
    usage parse. Non-JSON payloads (comments, keep-alives) are skipped.
    """
    last_usage = None
    for line in buffer.split(b"\n"):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[len(b"data:"):].strip()
        if not payload or payload == b"[DONE]":
            continue
        try:
            event = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        parsed = parse_stream_event(event) if isinstance(event, dict) else None
        if parsed is not None:
            last_usage = parsed
    return last_usage


# ── Proxy handler ─────────────────────────────────────────────────────


_EXCLUDE_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "authorization", "accept-encoding", "connection"}
)
_EXCLUDE_RESPONSE_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)


# Identity kinds allowed to traverse the LLM gateway reverse proxy.
# Agents are the expected caller for production traffic; machines are
# kept on the allowlist so future system-initiated calls (operational
# probes, gateway self-tests from the daemon) work without another
# auth layer. User tokens — even admin ones — are rejected: admin
# model-health checks go through ``/api/v1/llm-gateway/models/{id}/test``
# which uses the gateway master key directly and never enters this
# relay. Guest tokens never belong here at all: they are scoped to a
# single room chat and have no reason to issue LLM calls directly,
# so allowing them opens a cost-amplification path for anyone with
# an invite link.
_GATEWAY_IDENTITY_KINDS = frozenset({"agent", "machine"})


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def proxy(
    path: str,
    request: Request,
    background: BackgroundTasks,
    identity: Identity = Depends(get_current_identity),
    client: httpx.AsyncClient = Depends(get_upstream_client),
    supervisor: Any = Depends(get_supervisor),
    db: AsyncSession = Depends(get_db),  # noqa: ARG001 - usage logging needs factory
) -> Response:
    if identity.kind not in _GATEWAY_IDENTITY_KINDS:
        # Hide the existence of the proxy surface from unauthorized
        # callers by reusing the same 403 as other admin gates; the
        # detail message is intentionally generic so a user can't
        # enumerate which kinds are allowed.
        raise HTTPException(
            status_code=403,
            detail="LLM gateway is not available to this identity",
        )

    master_key = supervisor.master_key
    if master_key is None:
        raise HTTPException(status_code=503, detail="LLM gateway not ready")

    # Build upstream headers — strip hop-by-hop + the caller's token,
    # swap in the master key.
    upstream_headers: dict[str, str] = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _EXCLUDE_REQUEST_HEADERS
    }
    upstream_headers["authorization"] = f"Bearer {master_key}"

    body = await request.body()

    # Extract model name for usage logging before upstream call.
    model_name = ""
    if body:
        try:
            parsed_body = json.loads(body)
            if isinstance(parsed_body, dict):
                model_name = str(parsed_body.get("model") or "")
        except (ValueError, UnicodeDecodeError):
            pass

    # #420 — emit an ``llm.generation`` span and resolve which in-flight
    # request (if any) this call belongs to. ``room_id`` from the
    # returned correlation is stamped on the usage row. No-op (returns
    # None) when tracing is disabled.
    tracing = getattr(request.app.state, "tracing", None)
    agent_id = identity.id if identity.kind == "agent" else None

    def _correlate_llm(
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        duration_ms: int,
        status_code: int,
        response_body: bytes | None,
        error: str | None = None,
    ) -> str | None:
        if tracing is None:
            return None
        return tracing.record_llm_call(
            agent_id=agent_id,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
            status_code=status_code,
            request_body=body,
            response_body=response_body,
            error=error,
        ).room_id

    start = time.perf_counter()
    upstream_url = f"/{path}"

    try:
        upstream_resp = await client.request(
            method=request.method,
            url=upstream_url,
            headers=upstream_headers,
            content=body or None,
            params=dict(request.query_params),
        )
    except httpx.HTTPError as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        room_id = _correlate_llm(
            prompt_tokens=None,
            completion_tokens=None,
            duration_ms=duration_ms,
            status_code=502,
            response_body=None,
            error=f"upstream: {exc!r}"[:512],
        )
        background.add_task(
            _write_usage_row,
            request.app.state.session_factory,
            identity_kind=identity.kind,
            identity_id=identity.id,
            agent_id=agent_id,
            model_name=model_name,
            prompt_tokens=None,
            completion_tokens=None,
            duration_ms=duration_ms,
            status_code=502,
            error=f"upstream: {exc!r}"[:512],
            room_id=room_id,
        )
        raise HTTPException(status_code=502, detail="Upstream gateway error") from exc

    response_headers = {
        k: v
        for k, v in upstream_resp.headers.items()
        if k.lower() not in _EXCLUDE_RESPONSE_HEADERS
    }
    content_type = upstream_resp.headers.get("content-type", "")
    is_sse = content_type.startswith("text/event-stream")
    duration_ms = int((time.perf_counter() - start) * 1000)

    if is_sse:
        # For streaming we buffer the body (not ideal for very long
        # generations, but within the initial MVP scope where most
        # turns finish in seconds). This lets us parse the terminal
        # usage event and respond with a single ``Response`` carrying
        # the full stream content and correct headers. A follow-up
        # will switch to a true chunk-by-chunk relay with background
        # usage parsing.
        body_bytes = upstream_resp.content
        usage = _parse_sse_chunk_for_usage(body_bytes)
        room_id = _correlate_llm(
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            duration_ms=duration_ms,
            status_code=upstream_resp.status_code,
            response_body=body_bytes,
        )
        background.add_task(
            _write_usage_row,
            request.app.state.session_factory,
            identity_kind=identity.kind,
            identity_id=identity.id,
            agent_id=agent_id,
            model_name=model_name,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            duration_ms=duration_ms,
            status_code=upstream_resp.status_code,
            room_id=room_id,
        )
        return Response(
            content=body_bytes,
            status_code=upstream_resp.status_code,
            headers=response_headers,
            media_type="text/event-stream",
        )

    # Non-streaming response path.
    body_bytes = upstream_resp.content
    usage = None
    if body_bytes and "application/json" in content_type:
        try:
            parsed = json.loads(body_bytes)
            if isinstance(parsed, dict):
                usage = parse_json_usage(parsed)
        except ValueError:
            pass

    room_id = _correlate_llm(
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        duration_ms=duration_ms,
        status_code=upstream_resp.status_code,
        response_body=body_bytes,
    )
    background.add_task(
        _write_usage_row,
        request.app.state.session_factory,
        identity_kind=identity.kind,
        identity_id=identity.id,
        agent_id=agent_id,
        model_name=model_name,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        duration_ms=duration_ms,
        status_code=upstream_resp.status_code,
        room_id=room_id,
    )
    return Response(
        content=body_bytes,
        status_code=upstream_resp.status_code,
        headers=response_headers,
        media_type=content_type or None,
    )
