"""Prepare the local-room Pi bridge to the cluster's existing self MCP tools.

Only the room adapter calls this module. Federation invocations never receive
this configuration or the agent-scoped MCP credential. The extension is trusted
package code; server schemas are data, never interpolated JavaScript.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

TOKEN_ENV = "ANYGARDEN_AGENT_TOKEN"
CONFIG_ENV = "AG_PI_SELF_TOOLS_CONFIG"
EXTENSION_PATH = Path(__file__).with_name("pi_self_tools.mjs")
TOOL_NAMES = frozenset(
    {
        "create_skill",
        "update_skill",
        "list_my_skills",
        "delete_my_skill",
        "claim_task",
        "mark_task_status",
        "create_task",
        "add_task_blocker",
        "clear_task_blocker",
    }
)
MAX_RESPONSE_BYTES = 262_144


class PiSelfToolsError(ValueError):
    """A safe, operator-facing failure preparing local task/skill tools."""


def self_mcp_url(server_url: str) -> str:
    """Use only the explicitly configured chat server, never ambient endpoints."""
    parsed = urlsplit(server_url)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ord(char) < 33 for char in server_url)
    ):
        raise PiSelfToolsError(
            "Pi self tools require an explicit HTTP(S) chat server URL"
        )
    return urlunsplit(
        (scheme, parsed.netloc, parsed.path.rstrip("/") + "/mcp/rpc", "", "")
    )


def _validate_tools(payload: object) -> list[dict]:
    if (
        not isinstance(payload, dict)
        or payload.get("jsonrpc") != "2.0"
        or payload.get("id") != "pi-tools"
        or payload.get("error")
    ):
        raise PiSelfToolsError("Pi self tools: server rejected the tool-list request")
    result = payload.get("result")
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        raise PiSelfToolsError("Pi self tools: invalid tool-list response")
    selected = {}
    for tool in tools:
        if (
            not isinstance(tool, dict)
            or not isinstance(tool.get("name"), str)
            or tool["name"] not in TOOL_NAMES
        ):
            continue
        name = tool["name"]
        schema = tool.get("inputSchema")
        if (
            name in selected
            or not isinstance(tool.get("description"), str)
            or not isinstance(schema, dict)
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), dict)
        ):
            raise PiSelfToolsError("Pi self tools: invalid or duplicate tool schema")
        selected[name] = {
            "name": name,
            "description": tool["description"],
            "inputSchema": schema,
        }
    if set(selected) != TOOL_NAMES:
        raise PiSelfToolsError(
            "Pi self tools: the server does not provide all required task and skill tools"
        )
    return [selected[name] for name in sorted(selected)]


def _write_config(agent_root: Path, data: bytes) -> Path:
    """Atomic agent-local data file; do not follow workspace-created symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(agent_root, flags)
    temporary = f".self-tools-{secrets.token_hex(8)}.tmp"
    try:
        for component in (".anygarden-execution", "pi-self-tools"):
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, "config.json", src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    return agent_root / ".anygarden-execution" / "pi-self-tools" / "config.json"


async def prepare_pi_self_tools(
    agent_root: Path,
    *,
    server_url: str,
    token: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Path:
    """Authenticate before joining rooms; never substitute a transport token."""
    if (
        not token
        or not token.startswith("agt_")
        or len(token) > 1024
        or any(ord(char) < 33 or ord(char) > 126 for char in token)
    ):
        raise PiSelfToolsError(
            "Pi self tools: agent MCP credential is missing; restart this agent from its machine"
        )
    endpoint = self_mcp_url(server_url)
    try:
        async with (
            httpx.AsyncClient(
                timeout=10, follow_redirects=False, trust_env=False, transport=transport
            ) as client,
            client.stream(
                "POST",
                endpoint,
                headers={"Authorization": f"Bearer {token}"},
                json={"jsonrpc": "2.0", "id": "pi-tools", "method": "tools/list"},
            ) as response,
        ):
            if response.status_code in {401, 403}:
                raise PiSelfToolsError(
                    "Pi self tools: agent MCP authentication failed; restart this agent from its machine"
                )
            if response.status_code != 200:
                raise PiSelfToolsError(
                    f"Pi self tools: could not load tools (HTTP {response.status_code})"
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise PiSelfToolsError(
                        "Pi self tools: server tool list is too large"
                    )
    except httpx.HTTPError:
        raise PiSelfToolsError(
            "Pi self tools: could not reach the configured chat server"
        ) from None
    try:
        tools = _validate_tools(json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise PiSelfToolsError("Pi self tools: invalid tool-list response") from None
    config = json.dumps(
        {"endpoint": endpoint, "tools": tools}, ensure_ascii=False
    ).encode()
    return _write_config(agent_root, config)
