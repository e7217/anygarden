"""Base engine adapter — abstract interface for LLM integrations."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

from doorae_agent.coordination.pending_context import (
    drain_context,
    wrap_as_room_conversation,
)
from doorae_agent.integrations.cycle_guard import is_cycle_detected

if TYPE_CHECKING:
    from doorae_agent.client import ChatClient

logger = structlog.get_logger(__name__)


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

    Issue #286 — session-based adapters (Claude Code, Codex, Gemini
    CLI) all maintain a per-room ``_pending_context`` buffer for
    ``INGEST_ONLY`` messages and apply the same drain → wrap → concat
    pipeline before injecting the result into the next turn. The
    type annotation here lets the default ``assemble_user_content``
    method below operate on that buffer; concrete subclasses keep
    initializing the dict in their own ``__init__`` so init structure
    stays adapter-specific. Adapters that don't accumulate ambient
    context simply leave the buffer empty and the default method
    short-circuits to the bare input.
    """

    _pending_context: dict[str, list[tuple[float, str]]]

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

    def assemble_user_content(self, room_id: str, raw_content: str) -> str:
        """Standard user-content augmentation pipeline (#286).

        Drains the room's pending-context buffer (#74 / #148 Part 3),
        wraps any ambient lines in ``<room_conversation>`` XML
        (#284), and prepends the result to ``raw_content``. An
        empty buffer short-circuits the function so the adapter
        emits ``raw_content`` byte-identical to the bare-input path.

        Why this lives on the base class: pre-#286 each session
        adapter (Claude Code, Codex, Gemini CLI) inlined the same
        three-step block right above its engine call. Adding a new
        augmentation in #279 / #283 / #284 cost three identical
        edits per shipment. Centralising here means the next
        augmentation lands in one place and propagates to every
        session adapter automatically. Subclasses may override for
        engine-specific dedupe (codex's sha-tracked re-injection
        guard for *system-prompt* augmentation is a separate
        codepath and not affected).
        """
        prefix = drain_context(self._pending_context, room_id)
        if prefix:
            prefix = wrap_as_room_conversation(prefix)
        return f"{prefix}\n\n{raw_content}" if prefix else raw_content

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


def compose_memory_suffix(
    client: "ChatClient | None",
    room_id: str | None,
) -> str:
    """Shared helper — assemble the cross-engine memory / ephemeral block.

    Issue #237 — three engine adapters (Claude Code, Codex, Gemini CLI)
    all inject the same memory / ephemeral instructions into their
    respective system prompts. Centralising the read-from-client and
    ``compose_memory_block`` call here avoids duplicated logic and
    ensures all engines see the same content.

    Returns an empty string when the client is absent or the welcome
    frame has not arrived yet — adapters concatenate the result so a
    leading newline is only emitted when the block is non-empty.
    """
    if client is None:
        return ""

    # Both attributes are populated by the welcome frame handler in
    # ``doorae_agent.client``. Fall back defensively so pre-#237 clients
    # stay source-compatible.
    memory_md = getattr(client, "_memory_md", None)
    room_ephemeral_map = getattr(client, "_room_ephemeral", {}) or {}
    ephemeral = bool(room_ephemeral_map.get(room_id, False)) if room_id else False

    # #246 — room shared files live under ``<agent_root>/memory/shared``.
    # The agent subprocess's cwd is ``<agent_root>/workspace`` (the
    # spawner pins cwd there so engines discover AGENTS.md/CLAUDE.md
    # via their upward file-discovery walk), so ``memory/shared`` sits
    # one directory up from cwd — not inside it. ``Path.cwd() / "memory"
    # / "shared"`` was the original shape and silently evaluated to a
    # non-existent path, which made ``compose_shared_context_block``
    # return an empty block and the agent never saw room-shared files.
    from pathlib import Path

    from doorae_agent.memory import (
        compose_memory_block,
        compose_shared_context_block,
    )

    shared_block = compose_shared_context_block(
        Path.cwd().parent / "memory" / "shared"
    )

    # Skip the suffix entirely when nothing would be rendered — keeping
    # pre-#237 / pre-#246 prompts byte-for-byte identical in that case.
    if not memory_md and not ephemeral and not shared_block:
        return ""

    memory_block = (
        compose_memory_block(memory_md, ephemeral)
        if (memory_md or ephemeral)
        else ""
    )
    return memory_block + shared_block


def decide_policy(msg: dict[str, Any], client: ChatClient) -> MessagePolicy:
    """Unified 3-state gate: how should the agent handle this message?

    Returns ``MessagePolicy.{RESPOND, INGEST_ONLY, SKIP}``.

    Rules (evaluated in order):
    1. Own message → SKIP (self-echo prevention).
    2. ``[DELEGATED]`` / ``[ROOM_QUERY]`` prefix or ``room_query``
       metadata → RESPOND (task initiation / room-routed query
       always processed).
    2d. Cycle detection (#157 Phase B) → SKIP when the same
        (sender, content_hash) repeats within a small window.
    3. Server-parsed explicit mention matching this agent → RESPOND.
       The server's ``parse_mentions`` (``orchestration/rules.py``)
       drops non-word ``@`` tokens — e.g. ``alice@example.com`` or
       ``@dataclass`` — so we only see addressable mentions here.
       This mirrors how Slack/Discord route via resolved mentions
       instead of having every client re-parse raw text.
    4a. Strategy-forced RESPOND (#233). The server has singled us
        out as the rightful speaker: either this room's
        ``orchestrator_agent_id`` matches us, or the frame carries
        ``next_speaker_participant_id`` pointing at one of our
        participant ids. Evaluated *before* rule 4 so that a
        mis-stamped ``ingest_only=True`` (server bug, race, or
        future code path) can't silence the nominated speaker.
    4. ``metadata.ingest_only`` flag → INGEST_ONLY. The server or
       a broadcasting agent (typically the room representative's
       ``_deliver_result``) marks a message with this flag to say
       "every other listener should absorb this into their engine
       session context without replying". The canonical producers
       are the ``[취합 결과]`` broadcast on issue #74 and the ambient
       chatter stamp on #148 Part 3.
    5. **Explicit mentions present but NOT for us → SKIP.**
       Previously this rule fired for every agent in the room
       regardless of mention target, so a multi-agent room echoed N
       responses to a single-addressed message. When the server saw
       at least one addressable mention, treat the message as
       targeted and stay out unless rule 3 matched.
    6a. Strategy dispatcher tail (#159 Phase B/C). For
        ``round_robin`` / ``orchestrator`` rooms the "I speak"
        branches already ran in rule 4a; reaching here means we
        sit out (``round_robin`` SKIP, ``orchestrator`` O3 SKIP
        when an orchestrator exists).
    6. No addressable mentions + human sender → RESPOND. Covers 1:1
       DMs and "no one in particular" broadcasts on
       ``mentioned_only`` rooms, which was the pre-#159 default.
    7. Agent sender, no mention → SKIP (ignore unaddressed agent
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

    # 2d. Semantic cycle detection (#157 Phase B). The same (sender,
    # content_hash) pair repeating within a small window is a loop
    # that ``max_agent_turns`` and the task-init reset guard can't
    # catch — agents can emit distinct non-task-init content each
    # turn and still be repeating the same idea. Runs *before* the
    # mention rule so a determined @-chain can't force an agent to
    # restate the same reply forever. Short content has ``hash=None``
    # and is skipped inside ``is_cycle_detected``.
    room_id = msg.get("room_id")
    recent = (
        client._recent_msgs.get(room_id, ())
        if room_id and hasattr(client, "_recent_msgs")
        else ()
    )
    if is_cycle_detected(msg, recent):
        logger.warning(
            "decide_policy.cycle_detected",
            room_id=room_id,
            sender=sender,
        )
        return MessagePolicy.SKIP

    # 3. Directly mentioned → respond. Evaluated before the
    # ingest_only flag check so addressability always wins over
    # passive ingestion: a broadcast tagged for context that also
    # happens to mention us is still actionable work.
    if mentioned_me:
        return MessagePolicy.RESPOND

    # 4a. Strategy-forced RESPOND (#233). The server has already
    # singled this agent out as the rightful speaker for this frame
    # — either by pinning us as the room's orchestrator (O1 path)
    # or by stamping ``next_speaker_participant_id`` on us
    # (round_robin / orchestrator O2 path). Both cases MUST win
    # over rule 4's ``ingest_only`` short-circuit below, otherwise
    # an orchestrator that happens to receive a stamped frame
    # (server bug, race, or future feature) silently demotes its
    # own turn to passive ingestion and the room goes quiet. Moved
    # ahead of the stamp check as belt-and-suspenders to the server
    # fix in ``ws/handler.py::_is_ambient_candidate``: even if the
    # stamp sneaks through, the explicitly-nominated speaker still
    # acts. See ``_dispatch_strategy`` below for the full O2/O3
    # fallthrough that only fires when 4a/4 didn't decide.
    strategy_cache = getattr(client, "_speaker_strategy", None)
    strategy = (
        strategy_cache.get(room_id, "mentioned_only")
        if isinstance(strategy_cache, dict) and room_id
        else "mentioned_only"
    )
    if strategy == "orchestrator":
        orc_map = getattr(client, "_orchestrator_agent_id", None)
        orc_for_room_4a = (
            orc_map.get(room_id)
            if isinstance(orc_map, dict) and room_id
            else None
        )
        my_agent_id = getattr(client, "_agent_id", None)
        # O1: I am this room's orchestrator → RESPOND. Hoisted from
        # the strategy dispatcher below so it beats ``ingest_only``.
        if (
            orc_for_room_4a
            and my_agent_id
            and orc_for_room_4a == my_agent_id
        ):
            return MessagePolicy.RESPOND
    if strategy in ("round_robin", "orchestrator"):
        # next_speaker stamp points at me → RESPOND. Hoisted so a
        # mis-stamped frame can't silence the designated speaker.
        next_speaker = metadata.get("next_speaker_participant_id")
        if next_speaker and next_speaker in client._my_participant_ids:
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
    # Note: rule 4a above deliberately runs *first* so that
    # strategy-nominated speakers (orchestrator / round_robin
    # target) don't get short-circuited into INGEST_ONLY — #233.
    if metadata.get("ingest_only"):
        if getattr(client, "_context_window_opt_out", False):
            return MessagePolicy.SKIP
        return MessagePolicy.INGEST_ONLY

    # 5. Explicit mention list exists and does not include us →
    # someone else is being addressed, so we stay out. Stage B
    # (#74) promoted peer-mention messages here to INGEST_ONLY when
    # the local env accumulator opted in; with #148 Part 3 that
    # decision is made server-side via ``metadata.ingest_only``
    # (handled in rule 4 above), so we no longer need a second
    # agent-side gate.
    if addressable:
        return MessagePolicy.SKIP

    # Rules 6/7 — strategy dispatcher (#159 Phase B).
    # Up to this point every rule is strategy-independent (self-echo,
    # task-init, room_query, cycle, mention, ingest_only, mention-not-us
    # are all sacred across strategies). Below we branch on the room's
    # ``speaker_strategy`` — default ``mentioned_only`` preserves the
    # pre-#159 behaviour for every existing room. ``strategy`` was
    # already resolved above for rule 4a (#233); re-used here.

    if strategy == "round_robin":
        # Round-robin: the "my turn" branch was evaluated as part of
        # rule 4a above. Reaching here means next_speaker is absent
        # or points elsewhere → this agent sits out the turn. No
        # fallthrough to rule 6 — round_robin is strictly
        # server-dispatched, see ``TestRoundRobinStrategy``.
        return MessagePolicy.SKIP

    if strategy == "orchestrator":
        # #159 Phase C — O1/O2/O3. O1 ("I am this room's orchestrator")
        # and O2 ("next_speaker_participant_id points at me") were
        # hoisted to rule 4a so they beat ``ingest_only``. Reaching
        # this branch means neither condition fired.
        orc_map = getattr(client, "_orchestrator_agent_id", None)
        orc_for_room = (
            orc_map.get(room_id)
            if isinstance(orc_map, dict) and room_id
            else None
        )

        # Graceful fallback — strategy is 'orchestrator' but nobody is
        # pinned as one yet (admin flipped the knob without picking an
        # agent). Behave like mentioned_only so the room stays usable:
        # fall through to rule 6/7 below.
        if orc_for_room:
            # O3: orchestrator is set, I'm not it, next_speaker isn't
            # me → stay silent. This is the structural change that
            # lets the orchestrator genuinely sequence the room.
            return MessagePolicy.SKIP

    # 6. No addressable mention. Humans talking generally to the
    # room keep the historical "everyone replies" behaviour; this
    # preserves the 1:1 DM UX where no explicit mention is needed.
    sender_is_agent = bool(metadata.get("_nonce"))
    if not sender_is_agent:
        return MessagePolicy.RESPOND

    # 7. Agent sender, no mention → skip. The ambient-ingestion
    # promotion that Stage B performed here has moved to the
    # server-side ``ingest_only`` stamp (see rule 4 and cluster
    # ``ws/handler.py::_is_ambient_candidate``).
    return MessagePolicy.SKIP


def should_respond(msg: dict[str, Any], client: ChatClient) -> bool:
    """Back-compat wrapper: does ``decide_policy`` say RESPOND?

    Existing call sites (adapters, tests) keep the boolean contract.
    New handlers should call ``decide_policy`` directly to access the
    three-way ``INGEST_ONLY`` state introduced by #74.
    """
    return decide_policy(msg, client) == MessagePolicy.RESPOND
