"""JSON-RPC 2.0 endpoint for the agent-facing MCP server (#120).

Mounted as ``POST /mcp/rpc`` on the cluster FastAPI app.  Speaks the
minimum MCP subset needed for a tools-only server:

- ``initialize`` — version + capabilities
- ``tools/list`` — announce the four skill-authoring tools
- ``tools/call`` — dispatch to handlers in ``tools.py``

Each request authenticates via the same ``Authorization: Bearer
<agent-token>`` path HTTP API endpoints use — see ``auth.py`` for
the ``agent-only`` check.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from doorae.mcp.auth import resolve_agent_id
from doorae.mcp.tools import TOOL_SCHEMAS, call_tool, mark_task_status

router = APIRouter(prefix="/mcp", tags=["mcp"])


PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "doorae-skills", "version": "0.1.0"}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _jsonrpc_ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


@router.post("/rpc")
async def mcp_rpc(request: Request) -> dict[str, Any]:
    """Single-shot JSON-RPC endpoint.

    MCP technically prefers a long-lived transport (SSE or stdio) but
    a POST-per-call pattern is equally compliant for tools-only
    servers and keeps us inside FastAPI's standard request/response
    model — no background task bookkeeping required.
    """
    config = request.app.state.config
    auth_header = request.headers.get("authorization")

    # Parse body early so we can echo id on error responses.
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

    session_factory = request.app.state.session_factory
    async with session_factory() as db:
        agent_id = await resolve_agent_id(
            db,
            authorization=auth_header,
            jwt_secret=config.jwt_secret,
        )

    # ── initialize ──────────────────────────────────────────────
    if method == "initialize":
        return _jsonrpc_ok(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )

    # ── tools/list ──────────────────────────────────────────────
    if method == "tools/list":
        return _jsonrpc_ok(req_id, {"tools": TOOL_SCHEMAS})

    # ── tools/call ──────────────────────────────────────────────
    if method == "tools/call":
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            return _jsonrpc_error(req_id, -32602, "params must be an object")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _jsonrpc_error(req_id, -32602, "params.name is required")
        if not isinstance(arguments, dict):
            return _jsonrpc_error(
                req_id, -32602, "params.arguments must be an object"
            )
        # ``mark_task_status`` (#266) is the first tool that operates
        # on the main DB rather than the skill library, so it owns its
        # own session lifecycle here. The legacy skill tools below
        # continue to flow through ``call_tool``.
        if name == "mark_task_status":
            session_factory = request.app.state.session_factory
            async with session_factory() as db:
                tool_result = await mark_task_status(
                    db, agent_id=agent_id, arguments=arguments
                )
                if not tool_result.get("isError"):
                    # Snapshot the task and room name BEFORE the
                    # commit closes the session — fanout reads them
                    # outside the transaction boundary.
                    from sqlalchemy import select as _sa_select

                    from doorae.db.models import Room as _Room
                    from doorae.db.models import Task as _Task
                    from doorae.messages.service import (
                        fanout_task_event as _fanout_task_event,
                    )

                    task_id_arg = arguments.get("task_id")
                    task_obj = (
                        await db.execute(
                            _sa_select(_Task).where(_Task.id == task_id_arg)
                        )
                    ).scalar_one_or_none()
                    room_obj = None
                    if task_obj is not None:
                        room_obj = (
                            await db.execute(
                                _sa_select(_Room).where(
                                    _Room.id == task_obj.room_id
                                )
                            )
                        ).scalar_one_or_none()
                    await db.commit()
                    if task_obj is not None:
                        manager = getattr(
                            request.app.state, "connection_manager", None
                        )
                        await _fanout_task_event(
                            db,
                            manager=manager,
                            event="updated",
                            task=task_obj,
                            room_name=room_obj.name if room_obj else "",
                        )
            return _jsonrpc_ok(req_id, tool_result)

        service = _service(request)
        tool_result = await call_tool(service, agent_id, name, arguments)
        # Bump the author's generation when body-changing tools succeed,
        # so the lifecycle materializer re-runs on the next reconcile
        # and the new SKILL.md lands on disk.
        if not tool_result.get("isError") and name in {
            "create_skill",
            "update_skill",
            "delete_my_skill",
        }:
            lifecycle = getattr(request.app.state, "agent_lifecycle", None)
            if lifecycle is not None:
                await lifecycle.bump_generation(agent_id)
        return _jsonrpc_ok(req_id, tool_result)

    return _jsonrpc_error(req_id, -32601, f"method not found: {method}")


def _service(request: Request):
    service = getattr(request.app.state, "skill_library_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="skill_library_service not configured on app.state",
        )
    return service


__all__ = ["router", "mcp_rpc"]
