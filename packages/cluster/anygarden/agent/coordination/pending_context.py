"""Per-room pending-context buffer primitives for engine adapters.

Stage A (#74) introduced a buffer on ``ClaudeCodeAdapter`` that
absorbs ``INGEST_ONLY`` messages as context for the next active
turn. Stage B rolls the same pattern out to ``GeminiCliAdapter``
and ``CodexCliAdapter``. Rather than copy the TTL / size-cap / format
logic into each class, we factor the primitives here so all three
session-based adapters share one implementation.

Each adapter still owns its own ``_pending_context`` dict —
state is kept per-instance to preserve per-agent isolation (see
`docs/research/2026-04-19-multi-agent-context-injection.md`
Finding 3). This module contributes only pure functions operating
on a buffer the caller supplies.
"""

from __future__ import annotations

import time
from typing import Any

# Per-room upper bound. Each breadcrumb is short (≤300 chars after
# truncation), so ten entries cap the prefix at roughly 3 KB even
# in chatty rooms. The adapter calling ``append_context_line`` will
# evict FIFO-style once the limit hits.
PENDING_CONTEXT_MAX = 10

# Entries older than this are swept on next append/drain. Ten
# minutes is long enough to cover a ``[취합 결과]`` landing while
# the human composes a follow-up, short enough that a room resumed
# an hour later starts with a clean slate instead of stale chatter.
PENDING_CONTEXT_TTL_SEC = 600


def resolve_speaker_label(
    participant_id: str | None,
    roster: dict[str, Any] | None,
) -> str | None:
    """Resolve a human-readable speaker label for a participant (#538).

    Returns ``"{display_name}({kind})"`` when the room roster knows
    the participant, so the LLM can attribute a message to a *named*
    agent/human instead of an opaque id. Returns ``None`` when the
    roster is absent (pre-#221 server) or does not know the
    participant yet — callers decide the fallback (breadcrumbs fall
    back to ``@{id[:8]}``; the addressed-message prefix skips
    labelling entirely so a bare-id prefix never leaks into prose).
    """
    if roster and participant_id:
        brief = roster.get(participant_id)
        if isinstance(brief, dict):
            name = brief.get("display_name")
            if name:
                kind = brief.get("kind") or "user"
                return f"{name}({kind})"
    return None


def format_context_line(
    msg: dict[str, Any],
    roster: dict[str, Any] | None = None,
) -> str | None:
    """Render a one-line breadcrumb for injection into the next
    turn's prompt.

    Returns ``None`` when the message has nothing renderable (empty
    content after strip) so callers can skip the buffer write
    entirely. The ``[참고]`` label positions the line as external
    context rather than a fresh user turn the model must answer,
    and ``room_query_result`` gets a special locator so the reader
    can tell "this came from the cross-room query" vs "another
    participant spoke ambient".

    #538 — when ``roster`` is supplied the speaker is rendered as
    ``{display_name}({kind})`` so the model can tell *who* spoke
    (agent vs human vs self) instead of parroting an opaque
    ``@{id[:8]}``. Without a roster (or an unknown participant) the
    pre-#538 ``@{id[:8]}`` form is preserved for backward compat.
    """
    content = (msg.get("content") or "").strip()
    if not content:
        return None
    meta = msg.get("metadata") or {}
    snippet = content[:300]
    if "room_query_result" in meta:
        rq = meta["room_query_result"] or {}
        target = rq.get("target_room_id") or "?"
        return f"[참고] 룸 {target}에서 다음 응답이 왔습니다: {snippet}"
    pid = msg.get("participant_id")
    label = resolve_speaker_label(pid, roster) or f"@{(pid or 'unknown')[:8]}"
    return f"[참고] {label}: {snippet}"


def append_context_line(
    buffer: dict[str, list[tuple[float, str]]],
    room_id: str,
    line: str,
) -> None:
    """Push ``line`` into the room's buffer, pruning stale/overflow
    entries first.

    Prune-before-append gives the freshest messages a consistent
    FIFO guarantee: a chatty room keeps its tail of recent context
    instead of accumulating old noise that nothing will ever
    consume.
    """
    now = time.monotonic()
    cutoff = now - PENDING_CONTEXT_TTL_SEC
    buf = buffer.setdefault(room_id, [])
    buf[:] = [(t, line_) for t, line_ in buf if t >= cutoff]
    if len(buf) >= PENDING_CONTEXT_MAX:
        buf.pop(0)
    buf.append((now, line))


def drain_context(
    buffer: dict[str, list[tuple[float, str]]],
    room_id: str,
) -> str:
    """Pop the room's buffer into a joined prefix string.

    Applies the TTL sweep one more time here so a buffer that sat
    for hours without an active turn doesn't leak expired lines.
    Returns ``""`` when the buffer is empty or entirely stale so
    the caller skips prefix assembly altogether.
    """
    buf = buffer.pop(room_id, [])
    if not buf:
        return ""
    now = time.monotonic()
    cutoff = now - PENDING_CONTEXT_TTL_SEC
    lines = [line for ts, line in buf if ts >= cutoff]
    return "\n".join(lines)


# Issue #284 — preamble injected before the drained pending context
# inside the ``<room_conversation>`` wrapper. Spells out three things
# the LLM must follow:
#   1) what the wrapped block actually is (room conversation),
#   2) that the user has already seen these messages,
#   3) what NOT to do (relay / summarize back to the user).
# Phrased in Korean to match Anygarden's default operator + user locale
# and the surrounding system-prompt ergonomics (#283 collab hint also
# uses Korean examples). Localisation fallbacks belong to a separate
# follow-up.
ROOM_CONVERSATION_PREAMBLE = (
    "다음은 같은 방의 다른 참가자들이 나눈 대화입니다. "
    "사용자도 이 메시지들을 이미 보았습니다. "
    "답변하거나 요약하거나 사용자에게 다시 전달하지 마세요. "
    "현재 사용자 질문의 맥락으로만 사용하세요."
)


def wrap_as_room_conversation(prefix: str) -> str:
    """Wrap a drained pending-context prefix in a ``<room_conversation>``
    XML block (#284).

    Pre-#284 the drained prefix was prepended verbatim to the next
    turn's user content as ``[참고] ...\\n\\n<user_msg>``, which the
    LLM frequently mistook for a relay-target item — agents would
    "kindly forward" the peer's answer back to the user even though
    the user had already seen it in the room. XML wrapping plus a
    short Korean preamble shifts the block into "context, not input"
    territory; Anthropic's prompt guide explicitly recommends XML
    tags for this kind of structural disambiguation, and the same
    pattern works on Codex / Gemini engines we ship with.

    Empty input → empty output. Callers can keep their existing
    ``if prefix else <bare content>`` short-circuit and pre-#284
    prompts stay byte-identical when the buffer was empty.
    """
    if not prefix:
        return ""
    return (
        "<room_conversation>\n"
        f"{ROOM_CONVERSATION_PREAMBLE}\n\n"
        f"{prefix}\n"
        "</room_conversation>"
    )
