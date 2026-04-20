"""``/api/v1/llm/*`` reverse proxy (#197).

All agent LLM traffic enters doorae through this router. The handler
responsibilities:

1. Authenticate the caller via the existing identity dependency
   (``auth.dependencies``). Any of user / agent / machine tokens
   pass — an agent must be able to hit this to make its actual LLM
   call, so it is not admin-gated.
2. Replace the caller's ``Authorization`` header with
   ``Bearer <gateway-master-key>`` — the supervisor's ephemeral
   master key, shared in-process with the reverse proxy. The
   upstream ``litellm`` then authenticates against its own config.
3. Forward the request (method + headers + body or stream) to
   ``http://127.0.0.1:<port>/<path:path>`` via a long-lived
   ``httpx.AsyncClient``.
4. Relay the response as a ``StreamingResponse`` when SSE is
   negotiated, or a regular JSON ``Response`` otherwise.
5. After the response completes (or errors), record one row in
   ``llm_gateway_usage`` via the background task queue. Streaming
   responses get their usage parsed from the final event.

The router is only included in the app when
``settings.llm_gateway_enabled=True`` — feature flag off keeps this
path inert.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/llm", tags=["llm-gateway-proxy"])


# NOTE — route handlers intentionally unimplemented here. The TDD
# cycle in Phase 2 adds them against a fake upstream server
# (``aiohttp`` or ``httpx.MockTransport``), then wires the
# ``httpx.AsyncClient`` dependency in during ``lifespan``.
#
# Planned shape:
#
#   @router.api_route(
#       "/{path:path}",
#       methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
#   )
#   async def proxy(
#       path: str,
#       request: Request,
#       identity: Identity = Depends(resolve_identity),
#       client: httpx.AsyncClient = Depends(get_llm_client),
#       supervisor: LLMGatewaySupervisor = Depends(get_supervisor),
#       db: AsyncSession = Depends(get_db),
#   ) -> Response | StreamingResponse:
#       ...
#
# Keep this file deliberately thin until the dependencies exist so
# imports don't fan out into half-built modules.
