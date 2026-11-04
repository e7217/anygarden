"""Tools of the people-facing MCP endpoint ``/mcp/user`` (#990).

Every tool is a thin translation of REST calls made in-process against
the same app with the caller's own ``Authorization`` header. Permissions,
API key scope, validation and side effects (WS fan-out, agent turns) are
therefore exactly those of the REST API — nothing here re-implements them.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
from fastapi import Request

# ``wait_for_reply`` bounds: MCP clients time a tool call out after a few
# minutes, so a long agent turn is awaited by calling the tool again.
WAIT_DEFAULT_SECONDS = 60
WAIT_MAX_SECONDS = 120
WAIT_POLL_SECONDS = 2.0

_ROOM_ID = {"type": "string", "description": "Room id (from list_rooms)."}

USER_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_rooms",
        "description": "List the Anygarden rooms you can see.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_room",
        "description": (
            "Show a room and its participants. Agents have kind 'agent'; mention one in "
            "send_message with <@user:PARTICIPANT_ID>."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"room_id": _ROOM_ID},
            "required": ["room_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_messages",
        "description": (
            "Read a room's messages, oldest first. With since_seq, only messages after that "
            "seq; without it, the latest page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "room_id": _ROOM_ID,
                "since_seq": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["room_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_message",
        "description": (
            "Post a message as yourself. Agents answer only when mentioned "
            "(<@user:PARTICIPANT_ID> or @everyone); a room with one agent needs no mention. "
            "Returns the message with its seq — pass that seq to wait_for_reply."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "room_id": _ROOM_ID,
                "content": {"type": "string", "minLength": 1},
                "thread_root_id": {
                    "type": "string",
                    "description": "Reply in this message's thread instead of the main chat.",
                },
            },
            "required": ["room_id", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait_for_reply",
        "description": (
            "Wait until an agent posts in the room after after_seq and return that message. "
            f"Waits up to timeout_sec (default {WAIT_DEFAULT_SECONDS}, max {WAIT_MAX_SECONDS}). "
            "If nothing arrived, says whether an agent is still working — call again to keep waiting."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "room_id": _ROOM_ID,
                "after_seq": {"type": "integer", "minimum": 0},
                "timeout_sec": {"type": "integer", "minimum": 0, "maximum": WAIT_MAX_SECONDS},
            },
            "required": ["room_id", "after_seq"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_tasks",
        "description": "List a room's tasks, optionally only those with one status.",
        "inputSchema": {
            "type": "object",
            "properties": {"room_id": _ROOM_ID, "status": {"type": "string"}},
            "required": ["room_id"],
            "additionalProperties": False,
        },
    },
]

USER_TOOL_NAMES = frozenset(schema["name"] for schema in USER_TOOL_SCHEMAS)


class ToolError(Exception):
    """A REST call refused the tool; the message is shown to the model."""


def internal_client(request: Request) -> httpx.AsyncClient:
    """An in-process client that calls this app as the original caller."""
    peer = request.client
    transport = httpx.ASGITransport(
        app=request.app,
        client=(peer.host, peer.port) if peer else ("127.0.0.1", 0),
    )
    headers = {"Authorization": request.headers.get("authorization", "")}
    return httpx.AsyncClient(transport=transport, base_url="http://anygarden.internal", headers=headers)


async def call_rest(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    resp = await client.request(method, path, **kwargs)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = resp.text
        raise ToolError(f"{method} {path} failed ({resp.status_code}): {detail}")
    return resp.json() if resp.content else None


def _result(structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False, default=str)}],
        "structuredContent": structured,
    }


def _error(message: str) -> dict[str, Any]:
    return {"isError": True, "content": [{"type": "text", "text": message}]}


def _message(m: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "seq", "participant_id", "content", "root_message_id", "created_at")
    return {k: m.get(k) for k in keep}


def _room_id(arguments: dict[str, Any]) -> str:
    room_id = arguments.get("room_id")
    if not isinstance(room_id, str) or not room_id:
        raise ToolError("room_id is required")
    return room_id


async def _participants(client: httpx.AsyncClient, room_id: str) -> list[dict[str, Any]]:
    room = await call_rest(client, "GET", f"/api/v1/rooms/{room_id}")
    return [
        {"id": p["id"], "kind": p["kind"], "display_name": p.get("display_name", "")}
        for p in room.get("participants", [])
    ]


async def _list_rooms(client: httpx.AsyncClient, arguments: dict[str, Any]) -> dict[str, Any]:
    rooms = await call_rest(client, "GET", "/api/v1/rooms")
    keep = ("id", "name", "description", "project_id", "parent_room_id", "is_dm")
    return {"rooms": [{k: r.get(k) for k in keep} for r in rooms]}


async def _get_room(client: httpx.AsyncClient, arguments: dict[str, Any]) -> dict[str, Any]:
    room_id = _room_id(arguments)
    room = await call_rest(client, "GET", f"/api/v1/rooms/{room_id}")
    return {
        "id": room["id"],
        "name": room["name"],
        "description": room.get("description"),
        "participants": await _participants(client, room_id),
    }


async def _read_messages(client: httpx.AsyncClient, arguments: dict[str, Any]) -> dict[str, Any]:
    room_id = _room_id(arguments)
    params = {k: arguments[k] for k in ("since_seq", "limit") if k in arguments}
    messages = await call_rest(client, "GET", f"/api/v1/rooms/{room_id}/messages", params=params)
    return {"messages": [_message(m) for m in messages]}


async def _send_message(client: httpx.AsyncClient, arguments: dict[str, Any]) -> dict[str, Any]:
    room_id = _room_id(arguments)
    content = arguments.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ToolError("content is required")
    root = arguments.get("thread_root_id")
    path = (
        f"/api/v1/rooms/{room_id}/threads/{root}/messages"
        if root
        else f"/api/v1/rooms/{room_id}/messages"
    )
    return {"message": _message(await call_rest(client, "POST", path, json={"content": content}))}


async def _wait_for_reply(client: httpx.AsyncClient, arguments: dict[str, Any]) -> dict[str, Any]:
    room_id = _room_id(arguments)
    after_seq = arguments.get("after_seq")
    if not isinstance(after_seq, int) or after_seq < 0:
        raise ToolError("after_seq must be a non-negative integer")
    timeout = min(int(arguments.get("timeout_sec", WAIT_DEFAULT_SECONDS)), WAIT_MAX_SECONDS)
    agents = {p["id"]: p for p in await _participants(client, room_id) if p["kind"] == "agent"}
    if not agents:
        raise ToolError("This room has no agents to wait for")
    deadline = time.monotonic() + max(timeout, 0)
    seq = after_seq
    while True:
        messages = await call_rest(
            client,
            "GET",
            f"/api/v1/rooms/{room_id}/messages",
            params={"since_seq": seq, "limit": 200},
        )
        for m in messages:
            seq = max(seq, m["seq"])
            agent = agents.get(m.get("participant_id"))
            if agent is not None:
                return {"replied": True, "agent": agent["display_name"], "message": _message(m)}
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(WAIT_POLL_SECONDS)
    active = await call_rest(client, "GET", "/api/v1/turns/active")
    working = any(entry["room_id"] == room_id for entry in active)
    return {"replied": False, "agent_working": working, "last_seq": seq}


async def _list_tasks(client: httpx.AsyncClient, arguments: dict[str, Any]) -> dict[str, Any]:
    room_id = _room_id(arguments)
    params = {"status": arguments["status"]} if arguments.get("status") else {}
    tasks = await call_rest(client, "GET", f"/api/v1/rooms/{room_id}/tasks", params=params)
    keep = ("id", "title", "status", "assignee_participant_id", "created_at")
    return {"tasks": [{k: t.get(k) for k in keep} for t in tasks]}


_HANDLERS = {
    "list_rooms": _list_rooms,
    "get_room": _get_room,
    "read_messages": _read_messages,
    "send_message": _send_message,
    "wait_for_reply": _wait_for_reply,
    "list_tasks": _list_tasks,
}


async def call_user_tool(
    client: httpx.AsyncClient, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return _error(f"unknown tool: {name}")
    try:
        return _result(await handler(client, arguments))
    except ToolError as exc:
        return _error(str(exc))
