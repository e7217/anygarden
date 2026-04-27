"""Unit tests for the ``EngineAdapter`` base class default impls.

Issue #286 — ``assemble_user_content`` was promoted from the three
session adapters to the ABC so a future augmentation lands in one
place. The pipeline (drain → wrap → concat) must produce
byte-identical output to the pre-#286 inline blocks for every
session adapter; that contract is pinned here directly against the
base method via a minimal subclass, independently of the SDK
plumbing each adapter wraps around it.
"""

from __future__ import annotations

from typing import Any

import pytest

from doorae_agent.coordination.pending_context import append_context_line
from doorae_agent.integrations.base import EngineAdapter


class _BareAdapter(EngineAdapter):
    """Minimal concrete subclass for exercising base-class defaults.

    The two abstract methods are stubbed because the tests only
    poke at ``assemble_user_content``; spinning up an SDK isn't
    needed for this layer.
    """

    def __init__(self) -> None:
        self._pending_context = {}

    async def on_message(self, msg: dict[str, Any]) -> str | None:
        return None

    async def start(self) -> None:
        return None


class TestAssembleUserContent:
    def test_empty_buffer_returns_raw_content_byte_identical(self) -> None:
        """The dominant case (no pending context for this room) must
        produce the input verbatim. Pre-#286 prompts depended on this
        short-circuit and the contract is now codified at the base."""
        adapter = _BareAdapter()
        assert adapter.assemble_user_content("r1", "hello") == "hello"

    def test_single_line_wrapped_and_prepended(self) -> None:
        """One absorbed ambient line lands inside the wrapper, with
        the user message preserved verbatim *outside* the wrapper —
        the LLM still sees ``hello`` as its actual input."""
        adapter = _BareAdapter()
        append_context_line(
            adapter._pending_context, "r1", "[참고] @abc12345: 답변"
        )

        out = adapter.assemble_user_content("r1", "hello")

        assert "<room_conversation>" in out
        assert "</room_conversation>" in out
        assert "[참고] @abc12345: 답변" in out
        # User content is *outside* the wrapper, after the closing tag.
        assert out.index("</room_conversation>") < out.index("hello")
        # Buffer is consumed — single drain semantics preserved.
        assert "r1" not in adapter._pending_context

    def test_multiple_lines_all_inside_wrapper(self) -> None:
        adapter = _BareAdapter()
        append_context_line(
            adapter._pending_context, "r1", "[참고] @a: 첫줄"
        )
        append_context_line(
            adapter._pending_context, "r1", "[참고] @b: 둘째줄"
        )

        out = adapter.assemble_user_content("r1", "user msg")

        # Both lines live between the open/close tags.
        open_idx = out.index("<room_conversation>")
        close_idx = out.index("</room_conversation>")
        assert open_idx < out.index("[참고] @a") < close_idx
        assert open_idx < out.index("[참고] @b") < close_idx

    def test_per_room_isolation(self) -> None:
        """A line ingested for room r1 must not leak into room r2's
        next turn. Buffer keying was already correct in
        ``coordination.pending_context`` but the base method is the
        contract surface — pin it here too."""
        adapter = _BareAdapter()
        append_context_line(
            adapter._pending_context, "r1", "[참고] @a: r1 only"
        )

        out_r2 = adapter.assemble_user_content("r2", "r2 msg")

        assert out_r2 == "r2 msg"
        # r1's buffer survives untouched.
        assert "r1" in adapter._pending_context

    @pytest.mark.asyncio
    async def test_ingest_context_default_is_noop(self) -> None:
        """Pre-#286 contract preserved: bare adapters that don't
        opt into the buffer absorb nothing and return cleanly."""
        adapter = _BareAdapter()
        await adapter.ingest_context({"content": "x", "room_id": "r1"})
        # Buffer remains empty because the default ingest_context
        # is intentionally a no-op (session adapters override).
        assert adapter._pending_context == {}
