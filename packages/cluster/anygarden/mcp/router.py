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

from anygarden.mcp.auth import resolve_agent_id
from anygarden.mcp.tools import (
    TOOL_SCHEMAS,
    add_task_blocker,
    call_tool,
    clear_task_blocker,
    create_task,
    mark_task_status,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])


PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "anygarden-skills", "version": "0.1.0"}


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

                    from anygarden.db.models import Room as _Room
                    from anygarden.db.models import Task as _Task
                    from anygarden.messages.service import (
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
                    # #459 — resolve-wake may have returned dependent
                    # tasks to ``todo`` + injected fresh assignment
                    # mentions. Snapshot them (and their newest mention
                    # message) BEFORE commit so the WS fanout can wake the
                    # dependents' assignees live, mirroring create_task.
                    structured = tool_result.get("structuredContent") or {}
                    woken_ids = structured.get("woken") or []
                    woken_payloads: list[tuple[Any, Any, Any]] = []
                    if woken_ids:
                        from anygarden.db.models import (
                            Message as _Message,
                        )

                        for w_id in woken_ids:
                            w_task = (
                                await db.execute(
                                    _sa_select(_Task).where(_Task.id == w_id)
                                )
                            ).scalar_one_or_none()
                            if w_task is None:
                                continue
                            w_room = (
                                await db.execute(
                                    _sa_select(_Room).where(
                                        _Room.id == w_task.room_id
                                    )
                                )
                            ).scalar_one_or_none()
                            # The newest task_assignment mention for this
                            # dependent (just injected by the hook).
                            w_msgs = (
                                await db.execute(
                                    _sa_select(_Message)
                                    .where(_Message.room_id == w_task.room_id)
                                    .order_by(_Message.seq.desc())
                                    .limit(5)
                                )
                            ).scalars().all()
                            w_msg = None
                            for m in w_msgs:
                                meta = m.extra_metadata or {}
                                ta = meta.get("task_assignment")
                                if ta and ta.get("task_id") == w_task.id:
                                    w_msg = m
                                    break
                            woken_payloads.append((w_task, w_room, w_msg))

                    await db.commit()
                    manager = getattr(
                        request.app.state, "connection_manager", None
                    )
                    if task_obj is not None:
                        await _fanout_task_event(
                            db,
                            manager=manager,
                            event="updated",
                            task=task_obj,
                            room_name=room_obj.name if room_obj else "",
                        )
                    # Broadcast each woken dependent: the synthetic mention
                    # frame (so the assignee agent wakes) + a task.updated
                    # frame (so the 1차/2차 task views reflect todo again).
                    for w_task, w_room, w_msg in woken_payloads:
                        if manager is not None and w_msg is not None:
                            from anygarden.ws.protocol import (
                                MessageOut as _MessageOut,
                            )

                            await manager.broadcast(
                                w_task.room_id,
                                _MessageOut(
                                    id=w_msg.id,
                                    room_id=w_msg.room_id,
                                    participant_id=w_msg.participant_id,
                                    content=w_msg.content,
                                    seq=w_msg.seq,
                                    created_at=w_msg.created_at,
                                    metadata=w_msg.extra_metadata,
                                ),
                            )
                        await _fanout_task_event(
                            db,
                            manager=manager,
                            event="updated",
                            task=w_task,
                            room_name=w_room.name if w_room else "",
                        )
            return _jsonrpc_ok(req_id, tool_result)

        # ``create_task`` (#270) — orchestrator-only tool that drops a
        # task into the main DB and reuses the Phase 1 mention
        # injection. Same session-lifecycle pattern as
        # ``mark_task_status`` so the WS fanout can run after commit.
        if name == "create_task":
            session_factory = request.app.state.session_factory
            async with session_factory() as db:
                tool_result = await create_task(
                    db, agent_id=agent_id, arguments=arguments
                )
                if not tool_result.get("isError"):
                    from sqlalchemy import select as _sa_select

                    from anygarden.db.models import Room as _Room
                    from anygarden.db.models import Task as _Task
                    from anygarden.messages.service import (
                        fanout_task_event as _fanout_task_event,
                    )

                    structured = tool_result.get("structuredContent") or {}
                    task_id = structured.get("task_id")
                    task_obj = None
                    room_obj = None
                    if task_id:
                        task_obj = (
                            await db.execute(
                                _sa_select(_Task).where(_Task.id == task_id)
                            )
                        ).scalar_one_or_none()
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
                        # Broadcast both the synthetic message frame
                        # (room channel, so the chat stream renders the
                        # task card) and the task.updated frame (room +
                        # admin user fanout for the 1차/2차 views).
                        if task_obj.assignee_participant_id is not None:
                            from anygarden.db.models import (
                                Message as _Message,
                            )
                            from anygarden.ws.protocol import (
                                MessageOut as _MessageOut,
                            )

                            # The injection helper persisted exactly
                            # one mention message during this call;
                            # pull the latest task_assignment row.
                            recent_msgs = (
                                await db.execute(
                                    _sa_select(_Message)
                                    .where(_Message.room_id == task_obj.room_id)
                                    .order_by(_Message.seq.desc())
                                    .limit(5)
                                )
                            ).scalars().all()
                            for m in recent_msgs:
                                meta = m.extra_metadata or {}
                                ta = meta.get("task_assignment")
                                if (
                                    ta
                                    and ta.get("task_id") == task_obj.id
                                    and manager is not None
                                ):
                                    await manager.broadcast(
                                        task_obj.room_id,
                                        _MessageOut(
                                            id=m.id,
                                            room_id=m.room_id,
                                            participant_id=m.participant_id,
                                            content=m.content,
                                            seq=m.seq,
                                            created_at=m.created_at,
                                            metadata=m.extra_metadata,
                                        ),
                                    )
                                    break
                        await _fanout_task_event(
                            db,
                            manager=manager,
                            event="created",
                            task=task_obj,
                            room_name=room_obj.name if room_obj else "",
                        )
            return _jsonrpc_ok(req_id, tool_result)

        # ``add_task_blocker`` / ``clear_task_blocker`` (#459, Wave 2c) —
        # main-DB tools that mutate the task_blockers relation. They carry
        # no WS fanout of their own (the dependent only wakes when its
        # blocker later reaches terminal, which flows through the
        # ``mark_task_status`` branch above). Same session lifecycle so the
        # commit persists the edge.
        if name in ("add_task_blocker", "clear_task_blocker"):
            session_factory = request.app.state.session_factory
            async with session_factory() as db:
                handler = (
                    add_task_blocker
                    if name == "add_task_blocker"
                    else clear_task_blocker
                )
                tool_result = await handler(
                    db, agent_id=agent_id, arguments=arguments
                )
                if not tool_result.get("isError"):
                    await db.commit()
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
