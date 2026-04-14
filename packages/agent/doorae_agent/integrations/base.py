"""Base engine adapter — abstract interface for LLM integrations."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doorae_agent.client import ChatClient


class EngineAdapter(ABC):
    """Abstract base class for engine integrations.

    Each adapter bridges incoming chat messages to an LLM engine
    and returns the engine's response (or None to skip).
    """

    @abstractmethod
    async def on_message(self, msg: dict[str, Any]) -> str | None:
        """Process an incoming message frame.

        Return a response string to send back to the room,
        or None to not respond.
        """

    @abstractmethod
    async def start(self) -> None:
        """Initialize the engine (API clients, sessions, etc.)."""

    async def stop(self) -> None:
        """Cleanup resources. Override if the engine needs teardown."""


def should_respond(msg: dict[str, Any], client: ChatClient) -> bool:
    """Unified gate: should the agent process this message?

    Replaces scattered filters with a single decision point.
    Called at the top of each adapter's ``_handle`` function.

    Rules (evaluated in order):
    1. Own message → False (self-echo prevention)
    2. [DELEGATED] prefix → True (task initiation always processed)
    3. @mentioned by name → True
    4. Human message + agent is sole agent in room → True (1:1 chat)
    5. Otherwise → False (ignore unaddressed agent chatter)
    """
    content = msg.get("content", "")
    sender = msg.get("participant_id")
    metadata = msg.get("metadata") or {}

    # 1. Self-message — already filtered in _process_frame but
    #    belt-and-suspenders here too.
    if sender and sender in client._my_participant_ids:
        return False

    # 2. [DELEGATED] or [ROOM_QUERY] task → always respond
    if content.startswith("[DELEGATED]") or content.startswith("[ROOM_QUERY]"):
        return True

    # 2b. room_query metadata → representative agent should respond
    room_query = metadata.get("room_query")
    if room_query:
        return True

    # 3. @mention check — look in server-parsed mentions list first,
    #    then fall back to content scan for names with spaces.
    agent_name = client._agent_name
    mentions = metadata.get("mentions", [])
    if agent_name and agent_name in mentions:
        return True
    # Content scan: handles "@테스트 에이전트" (with space)
    if agent_name and f"@{agent_name}" in content:
        return True

    # 4. Human message (no _nonce) — respond if this is a direct
    #    conversation (no other agent chatter to worry about).
    sender_is_agent = bool(metadata.get("_nonce"))
    if not sender_is_agent:
        return True

    # 5. Agent message without mention → skip
    return False
