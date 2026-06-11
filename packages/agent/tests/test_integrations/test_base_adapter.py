"""Unit tests for the ``EngineAdapter`` base class default impls.

Issue #286 — ``assemble_user_content`` was promoted from the three
session adapters to the ABC so a future augmentation lands in one
place. The pipeline (drain → wrap → concat) must produce
byte-identical output to the pre-#286 inline blocks for every
session adapter; that contract is pinned here directly against the
base method via a minimal subclass, independently of the SDK
plumbing each adapter wraps around it.

Issue #293 — ``compose_session_context_suffix`` and
``ShaTrackedInjector`` are likewise centralised on the base module
so memory + roster injection logic does not have to drift
adapter-by-adapter. The contract for both helpers is pinned here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from anygarden_agent.coordination.pending_context import append_context_line
from anygarden_agent.integrations.base import (
    EngineAdapter,
    ShaTrackedInjector,
    compose_referenced_files_hint,
    compose_session_context_suffix,
)


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

    def test_shared_file_references_are_prepended(self) -> None:
        adapter = _BareAdapter()

        out = adapter.assemble_user_content(
            "r1",
            "please review this",
            {
                "references": [
                    {
                        "type": "shared_file",
                        "id": "file-1",
                        "name": "spec.md",
                        "storage_name": "spec.md",
                    }
                ]
            },
        )

        assert out == (
            "<referenced-files>\n"
            "- spec.md: memory/shared/spec.md\n"
            "</referenced-files>\n\n"
            "please review this"
        )

    def test_ambient_context_precedes_shared_file_references(self) -> None:
        adapter = _BareAdapter()
        append_context_line(
            adapter._pending_context, "r1", "[참고] @abc12345: 답변"
        )

        out = adapter.assemble_user_content(
            "r1",
            "hello",
            {
                "references": [
                    {
                        "type": "shared_file",
                        "id": "file-1",
                        "name": "spec.md",
                        "storage_name": "spec.md",
                    }
                ]
            },
        )

        assert out.index("<room_conversation>") < out.index(
            "<referenced-files>"
        )
        assert out.index("</referenced-files>") < out.index("hello")

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


class TestTurnInputStash:
    """#433 — per-room turn-input stash that the run_engine closure reads
    back to surface the engine prompt on the engine_call_finished frame."""

    def test_record_then_take_pops_the_value(self) -> None:
        adapter = _BareAdapter()
        adapter._record_turn_input("r1", "augmented input")
        assert adapter._take_turn_input("r1") == "augmented input"
        # read-once: a second take returns None (slot popped)
        assert adapter._take_turn_input("r1") is None

    def test_take_without_record_returns_none(self) -> None:
        adapter = _BareAdapter()
        assert adapter._take_turn_input("r1") is None

    def test_per_room_isolation(self) -> None:
        adapter = _BareAdapter()
        adapter._record_turn_input("r1", "for r1")
        adapter._record_turn_input("r2", "for r2")
        assert adapter._take_turn_input("r2") == "for r2"
        assert adapter._take_turn_input("r1") == "for r1"

    def test_latest_record_overwrites(self) -> None:
        adapter = _BareAdapter()
        adapter._record_turn_input("r1", "old")
        adapter._record_turn_input("r1", "new")
        assert adapter._take_turn_input("r1") == "new"

    def test_missing_room_id_or_text_is_noop(self) -> None:
        adapter = _BareAdapter()
        adapter._record_turn_input(None, "x")
        adapter._record_turn_input("r1", None)
        assert adapter._take_turn_input(None) is None
        assert adapter._take_turn_input("r1") is None


class TestComposeReferencedFilesHint:
    def test_empty_for_missing_references(self) -> None:
        assert compose_referenced_files_hint(None) == ""
        assert compose_referenced_files_hint({}) == ""
        assert compose_referenced_files_hint({"references": "x"}) == ""

    def test_filters_invalid_and_dedupes_by_path(self) -> None:
        out = compose_referenced_files_hint(
            {
                "references": [
                    {"type": "other", "name": "ignored"},
                    {
                        "type": "shared_file",
                        "name": "spec.md",
                        "storage_name": "spec.md",
                    },
                    {
                        "type": "shared_file",
                        "name": "duplicate.md",
                        "storage_name": "spec.md",
                    },
                    {
                        "type": "shared_file",
                        "name": "bad.md",
                        "storage_name": "../bad.md",
                    },
                ]
            }
        )

        assert out == (
            "<referenced-files>\n"
            "- spec.md: memory/shared/spec.md\n"
            "</referenced-files>"
        )


def _stub_client(
    *,
    memory_md: str | None = None,
    ephemeral: bool = False,
    roster: str = "",
) -> MagicMock:
    """Build a ``ChatClient`` stub with the attributes
    ``compose_memory_suffix`` reads (``_memory_md``, ``_room_ephemeral``)
    plus the ``compose_roster_suffix`` method that
    ``compose_session_context_suffix`` calls when the roster gate is on.
    """
    client = MagicMock()
    client._memory_md = memory_md
    client._room_ephemeral = {"r1": ephemeral} if ephemeral else {}
    client.compose_roster_suffix.return_value = roster
    return client


class TestComposeSessionContextSuffix:
    """#293 — memory + roster centralised assembler."""

    def test_empty_when_client_none(self) -> None:
        """Adapters that haven't wired a ``ChatClient`` see an empty
        suffix — preserves the pre-#293 source-compatible no-op when
        the client is absent."""
        out = compose_session_context_suffix(
            None, "r1", include_roster=True, with_collaborative_hint=True
        )
        assert out == ""

    def test_empty_when_no_signals(self) -> None:
        """Solo agent in a room without ephemeral / shared-context /
        memory.md — the suffix is empty and adapters preserve the
        pre-#237 prompt byte-for-byte."""
        client = _stub_client()
        out = compose_session_context_suffix(
            client, "r1", include_roster=False, with_collaborative_hint=False
        )
        assert out == ""
        # Roster gate off: helper must not even ask the client.
        client.compose_roster_suffix.assert_not_called()

    def test_memory_only_when_roster_gate_off(self) -> None:
        """Memory should fire even with the roster gate off — the
        gate only controls roster, not memory."""
        client = _stub_client(memory_md="# Personal memory\nfoo")
        out = compose_session_context_suffix(
            client, "r1", include_roster=False, with_collaborative_hint=False
        )
        # Memory block content surfaced
        assert "Personal memory" in out
        # Roster suppressed by the gate
        client.compose_roster_suffix.assert_not_called()

    def test_shared_context_reads_from_agent_root_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_root = tmp_path / "agent-root"
        shared = agent_root / "memory" / "shared"
        shared.mkdir(parents=True)
        (shared / "note.md").write_text("room file content")
        monkeypatch.chdir(agent_root)

        client = _stub_client()
        out = compose_session_context_suffix(
            client, "r1", include_roster=False, with_collaborative_hint=False
        )

        assert "note.md" in out
        assert "room file content" in out

    def test_roster_with_collaborative_hint(self) -> None:
        """Collaborative agent path: roster appears, hint flag is
        forwarded to ``compose_roster_suffix``."""
        client = _stub_client(roster="- alice (id: ...)")
        out = compose_session_context_suffix(
            client, "r1", include_roster=True, with_collaborative_hint=True
        )
        assert "alice" in out
        client.compose_roster_suffix.assert_called_once_with(
            "r1", with_collaborative_hint=True
        )

    def test_roster_without_collaborative_hint_for_orchestrator(self) -> None:
        """Orchestrator path (claude_code only): roster appears
        without the peer-mention usage hint — handoff_to MCP is the
        designated routing channel."""
        client = _stub_client(roster="- bob (id: ...)")
        out = compose_session_context_suffix(
            client, "r1", include_roster=True, with_collaborative_hint=False
        )
        assert "bob" in out
        client.compose_roster_suffix.assert_called_once_with(
            "r1", with_collaborative_hint=False
        )

    def test_memory_then_roster_order(self) -> None:
        """When both are present, memory comes first. This is the
        post-#293 standardised order."""
        client = _stub_client(
            memory_md="MEMORY_BLOCK_MARKER",
            roster="ROSTER_BLOCK_MARKER",
        )
        out = compose_session_context_suffix(
            client, "r1", include_roster=True, with_collaborative_hint=True
        )
        assert "MEMORY_BLOCK_MARKER" in out
        assert "ROSTER_BLOCK_MARKER" in out
        assert out.index("MEMORY_BLOCK_MARKER") < out.index("ROSTER_BLOCK_MARKER")

    def test_no_leading_or_trailing_newline(self) -> None:
        """Adapters concatenate this output in different ways
        (system-prompt append vs turn prefix). The helper must not
        bake a leading or trailing newline that would accumulate
        blank lines on either side."""
        client = _stub_client(roster="- alice (id: ...)")
        out = compose_session_context_suffix(
            client, "r1", include_roster=True, with_collaborative_hint=True
        )
        assert out
        assert not out.startswith("\n")
        assert not out.endswith("\n")


class TestShaTrackedInjector:
    """#293 — sha-tracked delta-labelled re-injection for codex-style
    history-accumulating sessions."""

    def test_first_turn_emits_blocks_without_label(self) -> None:
        """The very first emission for a room is the initial seed —
        no delta label, just the suffix bodies in order."""
        injector = ShaTrackedInjector()
        out = injector.apply(
            "r1",
            memory_suffix="MEMORY",
            roster_suffix="ROSTER",
            memory_label="[mem-update]",
            roster_label="[roster-update]",
        )
        assert "MEMORY" in out
        assert "ROSTER" in out
        assert "[mem-update]" not in out
        assert "[roster-update]" not in out
        # Memory before roster
        assert out.index("MEMORY") < out.index("ROSTER")

    def test_unchanged_inputs_emit_nothing(self) -> None:
        """A second call with the same inputs returns "" — the
        codex thread already has the block in its history, so
        re-injecting would be a duplicate paste."""
        injector = ShaTrackedInjector()
        injector.apply(
            "r1",
            memory_suffix="MEMORY",
            roster_suffix="ROSTER",
            memory_label="[m]",
            roster_label="[r]",
        )
        out = injector.apply(
            "r1",
            memory_suffix="MEMORY",
            roster_suffix="ROSTER",
            memory_label="[m]",
            roster_label="[r]",
        )
        assert out == ""

    def test_memory_change_triggers_labelled_reinjection(self) -> None:
        """When memory changes mid-session, the new memory block is
        emitted with its label so the model treats it as an update.
        Roster is unchanged so it is not re-emitted."""
        injector = ShaTrackedInjector()
        injector.apply(
            "r1",
            memory_suffix="MEMORY-V1",
            roster_suffix="ROSTER",
            memory_label="[mem-update]",
            roster_label="[roster-update]",
        )
        out = injector.apply(
            "r1",
            memory_suffix="MEMORY-V2",
            roster_suffix="ROSTER",
            memory_label="[mem-update]",
            roster_label="[roster-update]",
        )
        assert "MEMORY-V2" in out
        assert "[mem-update]" in out
        # Roster is byte-identical → should not re-appear.
        assert "ROSTER" not in out
        assert "[roster-update]" not in out

    def test_roster_change_triggers_labelled_reinjection(self) -> None:
        """The mirror of the memory case: roster changed, memory
        unchanged."""
        injector = ShaTrackedInjector()
        injector.apply(
            "r1",
            memory_suffix="MEMORY",
            roster_suffix="ROSTER-V1",
            memory_label="[mem-update]",
            roster_label="[roster-update]",
        )
        out = injector.apply(
            "r1",
            memory_suffix="MEMORY",
            roster_suffix="ROSTER-V2",
            memory_label="[mem-update]",
            roster_label="[roster-update]",
        )
        assert "ROSTER-V2" in out
        assert "[roster-update]" in out
        assert "MEMORY" not in out

    def test_per_room_isolation(self) -> None:
        """sha tracking is keyed by room — a turn in r1 must not
        suppress the first-injection seed for r2."""
        injector = ShaTrackedInjector()
        injector.apply(
            "r1",
            memory_suffix="MEMORY",
            roster_suffix="",
            memory_label="[m]",
            roster_label="[r]",
        )
        out_r2 = injector.apply(
            "r2",
            memory_suffix="MEMORY",
            roster_suffix="",
            memory_label="[m]",
            roster_label="[r]",
        )
        # r2 sees the seed, no label.
        assert "MEMORY" in out_r2
        assert "[m]" not in out_r2
