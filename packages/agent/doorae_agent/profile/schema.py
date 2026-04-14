"""Pydantic schema for agent profile YAML files."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class AgentProfile(BaseModel):
    """Schema for ``~/.doorae/agents/<name>.yaml``."""

    name: str
    engine: str  # e.g. "openai", "claude-code", "anthropic"
    system_prompt: str = "You are a helpful assistant."
    rooms: list[str] = []
    mcp_servers: list[str] = []
    model: str = ""

    # Optional metadata
    description: Optional[str] = None
    tags: list[str] = []
