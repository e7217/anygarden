"""Unit tests for ``coordination/pending_context`` primitives.

The buffer / TTL / formatting behaviour was previously only covered
through the engine adapters (Stage A/B). This module pins the
``wrap_as_room_conversation`` helper introduced in #284 and gives
future maintainers a place to grow direct unit tests for the rest
of the module without dragging an adapter fixture along.
"""

from __future__ import annotations

from anygarden_agent.coordination.pending_context import (
    ROOM_CONVERSATION_PREAMBLE,
    wrap_as_room_conversation,
)


class TestWrapAsRoomConversation:
    """Issue #284 — pending context drained from the buffer is wrapped
    in a ``<room_conversation>`` XML block before being prepended to
    the next turn's user content. The wrapping must be a no-op for
    empty input so unrelated turns stay byte-identical."""

    def test_empty_prefix_returns_empty_string(self) -> None:
        """A no-pending-context turn must produce no wrapper output —
        callers rely on this short-circuit to keep pre-#284 prompts
        unchanged when the buffer is empty."""
        assert wrap_as_room_conversation("") == ""

    def test_non_empty_prefix_includes_xml_tags(self) -> None:
        prefix = "[참고] @abc12345: 비행 8시 출발입니다."
        wrapped = wrap_as_room_conversation(prefix)
        assert wrapped.startswith("<room_conversation>\n")
        assert wrapped.endswith("\n</room_conversation>")

    def test_wrapped_output_contains_preamble(self) -> None:
        """The Korean preamble — the LLM-facing instruction — must
        actually land inside the wrapper, not be silently dropped by
        a future refactor."""
        prefix = "[참고] @abc12345: 답변..."
        wrapped = wrap_as_room_conversation(prefix)
        assert ROOM_CONVERSATION_PREAMBLE in wrapped
        # And it appears *before* the prefix content, not after — the
        # preamble has to set context for the lines that follow.
        assert wrapped.index(ROOM_CONVERSATION_PREAMBLE) < wrapped.index(prefix)

    def test_wrapped_output_preserves_original_prefix(self) -> None:
        """The drained lines themselves must reach the LLM verbatim —
        the wrapper is structural, not transformative."""
        prefix = (
            "[참고] @abc12345: 첫 번째 줄\n"
            "[참고] @def67890: 두 번째 줄"
        )
        wrapped = wrap_as_room_conversation(prefix)
        assert prefix in wrapped

    def test_preamble_has_no_relay_instruction(self) -> None:
        """Regression guard: the preamble must explicitly tell the LLM
        NOT to relay or summarize. Earlier drafts in #279 / #283 went
        the opposite direction (synthesize → relay-style answers); the
        whole point of #284 is to flip that signal."""
        # Korean key phrases — the assertion catches a future copy
        # edit that accidentally drops the "do not" half of the
        # instruction.
        assert "전달하지 마세요" in ROOM_CONVERSATION_PREAMBLE
        assert "맥락으로만" in ROOM_CONVERSATION_PREAMBLE
