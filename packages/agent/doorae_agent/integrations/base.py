"""Base engine adapter — abstract interface for LLM integrations."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doorae_agent.client import ChatClient


class MessagePolicy(Enum):
    """Decision for how an incoming message should be handled.

    ``decide_policy`` returns one of these three states. The two
    original boolean outcomes (respond / skip) are still present —
    ``INGEST_ONLY`` is the new third state that lets a message update
    the agent's LLM-side context without triggering a response turn.

    - ``RESPOND``: generate a reply. The adapter calls ``on_message``.
    - ``INGEST_ONLY``: do not reply, but do feed this message into
      the engine's session as context for the next active turn. The
      adapter calls ``ingest_context``.
    - ``SKIP``: ignore the message entirely.
    """

    RESPOND = "respond"
    INGEST_ONLY = "ingest_only"
    SKIP = "skip"


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

    async def ingest_context(self, msg: dict[str, Any]) -> None:
        """Absorb a message into the engine's context without replying.

        Called by the handler when ``decide_policy`` returns
        ``INGEST_ONLY``. Default is a no-op so adapters that haven't
        opted in stay source-compatible — they simply drop the
        ambient message as before.

        Session-based adapters (Claude Code, Gemini CLI, Codex)
        override this to stash ``msg`` in a per-room buffer and
        consume it as a prompt prefix on the next ``on_message``
        call. Raw-SDK adapters that already own the message history
        can manage ingestion internally and leave this as a no-op.
        """
        return


def decide_policy(msg: dict[str, Any], client: ChatClient) -> MessagePolicy:
    """Unified 3-state gate: how should the agent handle this message?

    Returns ``MessagePolicy.{RESPOND, INGEST_ONLY, SKIP}``.

    Rules (evaluated in order):
    1. Own message → SKIP (self-echo prevention).
    2. ``[DELEGATED]`` / ``[ROOM_QUERY]`` prefix or ``room_query``
       metadata → RESPOND (task initiation / room-routed query
       always processed).
    2c. ``metadata.ingest_only`` flag → INGEST_ONLY. The server or
        a broadcasting agent (typically the room representative's
        ``_deliver_result``) marks a message with this flag to say
        "every other listener should absorb this into their engine
        session context without replying". The canonical producer is
        the ``[취합 결과]`` broadcast on issue #74.
    3. Server-parsed explicit mention matching this agent → RESPOND.
       The server's ``parse_mentions`` (``orchestration/rules.py``)
       drops non-word ``@`` tokens — e.g. ``alice@example.com`` or
       ``@dataclass`` — so we only see addressable mentions here.
       This mirrors how Slack/Discord route via resolved mentions
       instead of having every client re-parse raw text.
    4. **Explicit mentions present but NOT for us → SKIP.**
       Previously rule 4 ("human message → respond") fired for
       every agent in the room regardless of mention target, so a
       multi-agent room echoed N responses to a single-addressed
       message. When the server saw at least one addressable
       mention, treat the message as targeted and stay out unless
       rule 3 matched.
    5. No addressable mentions + human sender → RESPOND. Covers 1:1
       DMs and "no one in particular" broadcasts where the previous
       behaviour (respond) is still the most useful default.
    6. Agent sender, no mention → SKIP (ignore unaddressed agent
       chatter so agents don't ping-pong forever).
    """
    content = msg.get("content", "")
    sender = msg.get("participant_id")
    metadata = msg.get("metadata") or {}

    # 1. Self-message — already filtered in _process_frame but
    #    belt-and-suspenders here too.
    if sender and sender in client._my_participant_ids:
        return MessagePolicy.SKIP

    # 2. [DELEGATED] or [ROOM_QUERY] task → always respond
    if content.startswith("[DELEGATED]") or content.startswith("[ROOM_QUERY]"):
        return MessagePolicy.RESPOND

    # 2b. room_query metadata → only the representative agent forwards.
    # Issue #61 — the server now tags the broadcast with
    # ``representative_agent_id``. Non-representative agents in the
    # same source room MUST stay out, otherwise each fans out a
    # duplicate ``[ROOM_QUERY]`` to the target room. The legacy
    # fallback (``True``) covers two transition cases:
    # 1. Pre-#61 servers don't set ``representative_agent_id``.
    # 2. Pre-#61 clients don't populate ``_agent_id``.
    # Both can be removed once the whole fleet is on ≥#61.
    room_query = metadata.get("room_query")
    if room_query:
        rep_id = room_query.get("representative_agent_id")
        my_agent_id = getattr(client, "_agent_id", None)
        if rep_id and my_agent_id:
            return (
                MessagePolicy.RESPOND if my_agent_id == rep_id
                else MessagePolicy.SKIP
            )
        return MessagePolicy.RESPOND

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

    # 3. Directly mentioned → respond. Evaluated before the
    # ingest_only flag check so addressability always wins over
    # passive ingestion: a broadcast tagged for context that also
    # happens to mention us is still actionable work.
    if mentioned_me:
        return MessagePolicy.RESPOND

    # 4. Explicit ingest-only flag (#74 Stage A, #148 Part 3). Placed
    # *after* the addressability rule so a direct mention still gets
    # RESPOND. From this point the legacy gate would return SKIP or
    # RESPOND based on sender kind; the ingest_only flag short-
    # circuits that into passive ingestion instead. Producers:
    # - ``room_query._deliver_result`` (``[취합 결과]``)
    # - #148 Part 3: cluster ``ws/handler.py`` on ambient broadcasts
    #   in rooms where ``context_window_enabled`` is True.
    # #148 Part 3 opt-out: when this agent has the DB flag set, even
    # an ingest_only broadcast is dropped. The flag is refreshed on
    # every welcome frame (client.py) so a UI toggle + respawn
    # propagates without a protocol round-trip.
    if metadata.get("ingest_only"):
        if getattr(client, "_context_window_opt_out", False):
            return MessagePolicy.SKIP
        return MessagePolicy.INGEST_ONLY

    # 5. Explicit mention list exists and does not include us →
    # someone else is being addressed, so we stay out. This is the
    # fix for the "every agent in the room responds" bug — rule 6
    # below used to short-circuit it.
    if addressable:
        # Stage B promotion (#74): a mention aimed at a peer is the
        # canonical "ambient but informative" case. If the window
        # is enabled, the peer's response will be relevant to us
        # shortly — keep it available as context instead of
        # dropping it on the floor.
        if _ambient_capture_enabled(msg, client):
            return MessagePolicy.INGEST_ONLY
        return MessagePolicy.SKIP

    # 6. No addressable mention. Humans talking generally to the
    # room keep the historical "everyone replies" behaviour; this
    # preserves the 1:1 DM UX where no explicit mention is needed.
    sender_is_agent = bool(metadata.get("_nonce"))
    if not sender_is_agent:
        return MessagePolicy.RESPOND

    # 7. Agent sender, no mention → skip. Stage B promotion: agent-
    # to-agent chatter (e.g. another agent replying to the human in
    # a shared room) is exactly the ambient signal the window is
    # meant to capture when enabled.
    if _ambient_capture_enabled(msg, client):
        return MessagePolicy.INGEST_ONLY
    return MessagePolicy.SKIP


def _ambient_capture_enabled(
    msg: dict[str, Any], client: ChatClient
) -> bool:
    """Ask the Stage B accumulator whether this would-be-SKIP
    message should be promoted to INGEST_ONLY instead.

    Import is local so the Python agent can start up even if the
    coordination module ever fails to load: we'd rather ship the
    original 2-state behaviour than crash every adapter.
    """
    try:
        from doorae_agent.coordination.accumulator import get_accumulator
    except ImportError:  # pragma: no cover - defensive
        return False
    return get_accumulator().should_capture(msg, client)


def should_respond(msg: dict[str, Any], client: ChatClient) -> bool:
    """Back-compat wrapper: does ``decide_policy`` say RESPOND?

    Existing call sites (adapters, tests) keep the boolean contract.
    New handlers should call ``decide_policy`` directly to access the
    three-way ``INGEST_ONLY`` state introduced by #74.
    """
    return decide_policy(msg, client) == MessagePolicy.RESPOND
