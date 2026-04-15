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
    1. Own message → False (self-echo prevention).
    2. ``[DELEGATED]`` / ``[ROOM_QUERY]`` prefix or ``room_query``
       metadata → True (task initiation / room-routed query
       always processed).
    3. Server-parsed explicit mention matching this agent → True.
       The server's ``parse_mentions`` (``orchestration/rules.py``)
       drops non-word ``@`` tokens — e.g. ``alice@example.com`` or
       ``@dataclass`` — so we only see addressable mentions here.
       This mirrors how Slack/Discord route via resolved mentions
       instead of having every client re-parse raw text.
    4. **Explicit mentions present but NOT for us → False.**
       Previously rule 4 ("human message → respond") fired for
       every agent in the room regardless of mention target, so a
       multi-agent room echoed N responses to a single-addressed
       message. When the server saw at least one addressable
       mention, treat the message as targeted and stay out unless
       rule 3 matched.
    5. No addressable mentions + human sender → True. Covers 1:1
       DMs and "no one in particular" broadcasts where the previous
       behaviour (respond) is still the most useful default.
    6. Agent sender, no mention → False (ignore unaddressed agent
       chatter so agents don't ping-pong forever).
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
    if metadata.get("room_query"):
        return True

    agent_name = client._agent_name
    raw_mentions = metadata.get("mentions") or []

    # Only ``user``/``legacy`` mentions route to a specific participant.
    # Room mentions drive cross-room queries and are handled separately
    # above via ``room_query`` metadata, so they shouldn't force a skip
    # here (e.g. ``<#room:xyz>`` alone should not silence the room).
    addressable: list[dict[str, Any]] = [
        m
        for m in raw_mentions
        if isinstance(m, dict) and m.get("type") in ("user", "legacy")
    ]

    # Normalise both sides so "alice" / "Alice" match. Without this
    # the bug we're fixing would flip polarity: if the server's
    # legacy regex captured "@alice" but the agent registered as
    # "Alice", rule 3 would miss and rule 4 would silence the
    # agent. Casefold handles Unicode case-insensitivity.
    agent_key = agent_name.casefold() if agent_name else None

    def _targets_me(m: dict[str, Any]) -> bool:
        if m.get("type") == "legacy":
            name = m.get("name")
            return bool(agent_key) and isinstance(name, str) and name.casefold() == agent_key
        if m.get("type") == "user":
            # ID-based mention from the frontend autocomplete — the
            # ``id`` is a ``participant_id``, which is exactly what
            # the server puts in our welcome frame and we cache in
            # ``_my_participant_ids``. Same namespace both ends, so
            # ``in`` is an exact match (no casefold, no substring
            # trap). Previously this branch returned False and the
            # gate silenced every agent whenever the UI's token
            # format was in play — including the ``@<guest>`` case
            # where the addressee isn't an agent at all.
            target = m.get("id")
            return bool(target) and target in client._my_participant_ids
        return False

    mentioned_me = any(_targets_me(m) for m in addressable)
    # Backward-compat: the server's legacy pattern ``@([\w-]+)`` can't
    # span whitespace, so names like "@테스트 에이전트" never land in
    # ``addressable`` as a single mention. The content scan is a
    # last-resort fallback that recognises *this agent* directly.
    #
    # ``(?![\w:])`` is load-bearing: without it the scan matches
    # substrings, so an agent literally named ``user`` would
    # falsely flag the ID-based token ``<@user:<pid>>`` as a hit,
    # re-opening the fan-out bug. A word-or-colon lookahead stops
    # the match at ``@user`` followed by ``:``, while still
    # allowing ``@테스트 에이전트 안녕`` (space after the name).
    if not mentioned_me and agent_name:
        pattern = rf"@{re.escape(agent_name)}(?![\w:])"
        if re.search(pattern, content, re.IGNORECASE):
            mentioned_me = True

    # 3. Directly mentioned → respond.
    if mentioned_me:
        return True

    # 4. Explicit mention list exists and does not include us →
    # someone else is being addressed, so we stay out. This is the
    # fix for the "every agent in the room responds" bug — rule 5
    # below used to short-circuit it.
    if addressable:
        return False

    # 5. No addressable mention. Humans talking generally to the
    # room keep the historical "everyone replies" behaviour; this
    # preserves the 1:1 DM UX where no explicit mention is needed.
    sender_is_agent = bool(metadata.get("_nonce"))
    if not sender_is_agent:
        return True

    # 6. Agent sender, no mention → skip
    return False
