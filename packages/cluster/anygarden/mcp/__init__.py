"""Agent-facing MCP server (#120).

Exposes ``POST /mcp/rpc`` on the cluster FastAPI app so agents
(authenticated with an ``AgentToken``) can self-author skills via
the MCP ``create_skill / update_skill / list_my_skills /
delete_my_skill`` tools.

The implementation ships the minimum JSON-RPC 2.0 subset the MCP
spec mandates for a tools-only server:

- ``initialize`` — version + capability handshake
- ``tools/list`` — four tools with JSON Schemas
- ``tools/call`` — dispatch into ``tools/skills.py`` handlers

Keeping this dependency-light (no ``mcp`` SDK) is the plan §2.4 /
§2.5 decision — the cluster already has FastAPI, and the MCP
subset we need is a few dozen lines of glue.
"""

from __future__ import annotations

from anygarden.mcp.router import router

__all__ = ["router"]
