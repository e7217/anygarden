"""Unit tests for ``anygarden_agent.memory.compose_memory_block`` (#237)."""

from __future__ import annotations

from anygarden_agent.memory import compose_memory_block


class TestComposeMemoryBlock:
    def test_empty_memory_renders_placeholder(self) -> None:
        block = compose_memory_block(None, ephemeral=False)
        assert "<memory>" in block
        assert "</memory>" in block
        assert "아직 기억이 비어" in block
        # Policy section always present even when memory is empty so
        # the agent knows how to start writing.
        assert "<memory-policy>" in block
        # Ephemeral section absent when not requested.
        assert "<ephemeral-session>" not in block

    def test_whitespace_only_memory_is_treated_as_empty(self) -> None:
        block = compose_memory_block("   \n\n\t", ephemeral=False)
        assert "아직 기억이 비어" in block

    def test_populated_memory_is_embedded_verbatim(self) -> None:
        notes = "## 사용자 선호\n- 한국어를 선호함.\n- 간결한 답변 선호."
        block = compose_memory_block(notes, ephemeral=False)
        assert "## 사용자 선호" in block
        assert "한국어를 선호함" in block
        # Ensure the placeholder text is not leaked in when there is
        # real content.
        assert "아직 기억이 비어" not in block

    def test_ephemeral_adds_directive_block(self) -> None:
        block = compose_memory_block("some notes", ephemeral=True)
        assert "<ephemeral-session>" in block
        assert "</ephemeral-session>" in block
        # The directive must explicitly tell the agent not to write.
        assert "절대 기록하지 마세요" in block

    def test_ephemeral_false_never_emits_directive(self) -> None:
        block = compose_memory_block("notes", ephemeral=False)
        assert "ephemeral-session" not in block

    def test_block_always_ends_with_newline(self) -> None:
        # Adapter concatenation relies on a trailing newline so the
        # caller can keep using simple f-strings.
        for memory in (None, "", "foo"):
            for ephemeral in (False, True):
                block = compose_memory_block(memory, ephemeral=ephemeral)
                assert block.endswith("\n"), (memory, ephemeral)

    def test_ordering_memory_then_policy_then_ephemeral(self) -> None:
        block = compose_memory_block("x", ephemeral=True)
        i_mem = block.index("<memory>")
        i_policy = block.index("<memory-policy>")
        i_eph = block.index("<ephemeral-session>")
        assert i_mem < i_policy < i_eph
