"""People-facing MCP endpoint ``POST /mcp/user`` (#990).

Lets an LLM client such as Claude Code use Anygarden as a tool with a
personal API key (#988): ``Authorization: Bearer agu_…``. It is kept apart
from the agent channel ``/mcp/rpc`` — an agent token is refused here and a
personal key is refused there — because the two identities carry different
permission models and tool sets.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from anygarden import __version__
from anygarden.auth.api_key import API_KEY_PREFIX
from anygarden.mcp.jsonrpc import (
    PROTOCOL_VERSION,
    jsonrpc_error,
    jsonrpc_ok,
    read_call,
    tool_call_params,
)
from anygarden.mcp.user_tools import USER_TOOL_SCHEMAS, call_user_tool, internal_client

router = APIRouter(prefix="/mcp", tags=["mcp"])

SERVER_INFO = {"name": "anygarden", "version": __version__}
INSTRUCTIONS = (
    "Anygarden is a workspace where people and AI agents talk in rooms. "
    "Find a room with list_rooms, see its agents with get_room, post with "
    "send_message (mention an agent so it answers), then call wait_for_reply "
    "with the returned seq."
)


@router.post("/user", response_model=None)
async def mcp_user(request: Request) -> dict[str, Any] | Response:
    """Tools-only JSON-RPC endpoint for people (same framing as ``/mcp/rpc``)."""
    call = await read_call(request)
    if isinstance(call, Response):
        return call
    req_id, method, payload = call

    token = (request.headers.get("authorization") or "")[7:].strip()
    if not token.startswith(API_KEY_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This MCP endpoint takes a personal API key (agu_…)",
        )
    async with internal_client(request) as client:
        # Authenticate through the REST path itself so key validation,
        # revocation and the wrong-key limit match the API exactly.
        resp = await client.get("/api/v1/auth/me")
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=resp.json().get("detail"),
                headers={k: v for k, v in resp.headers.items() if k.lower() == "retry-after"},
            )

        if method == "initialize":
            return jsonrpc_ok(
                req_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": INSTRUCTIONS,
                },
            )
        if method == "tools/list":
            return jsonrpc_ok(req_id, {"tools": USER_TOOL_SCHEMAS})
        if method == "tools/call":
            parsed = tool_call_params(req_id, payload)
            if isinstance(parsed, dict):
                return parsed
            name, arguments = parsed
            return jsonrpc_ok(req_id, await call_user_tool(client, name, arguments))
        if method == "ping":
            return jsonrpc_ok(req_id, {})
    return jsonrpc_error(req_id, -32601, f"Method not found: {method}")

