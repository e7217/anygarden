"""JSON-RPC 2.0 framing shared by the MCP endpoints (#120, #990).

Both ``POST /mcp/rpc`` (agents) and ``POST /mcp/user`` (people with an
API key) speak the same tools-only Streamable-HTTP subset: one POST per
call, notifications acknowledged with ``202`` and an empty body (#352).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, Response, status

PROTOCOL_VERSION = "2025-03-26"


def jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def jsonrpc_ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


async def read_call(request: Request) -> tuple[Any, Any, dict[str, Any]] | Response:
    """Return ``(id, method, payload)``, or the ``202`` ack for a notification.

    JSON-RPC notifications carry no ``id`` and MUST NOT receive a
    response body. Acking them with 202 + empty body lets a strict MCP
    Streamable HTTP client (codex/rmcp) complete its handshake instead
    of aborting on an unexpected ``-32601`` response (#352). The ack is
    unconditional: notifications trigger no handler and need no auth.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON body",
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expected JSON object",
        )
    req_id = payload.get("id")
    method = payload.get("method")
    if req_id is None or (
        isinstance(method, str) and method.startswith("notifications/")
    ):
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return req_id, method, payload


def tool_call_params(req_id: Any, payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | dict[str, Any]:
    """Return ``(name, arguments)`` of a ``tools/call``, or its JSON-RPC error."""
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return jsonrpc_error(req_id, -32602, "params must be an object")
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str):
        return jsonrpc_error(req_id, -32602, "params.name is required")
    if not isinstance(arguments, dict):
        return jsonrpc_error(req_id, -32602, "params.arguments must be an object")
    return name, arguments
