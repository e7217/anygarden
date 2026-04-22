"""Tests for ``compose_shared_context_block`` (#246)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from doorae_agent.memory import compose_shared_context_block


class TestComposeSharedContextBlock:
    def test_none_returns_empty(self) -> None:
        assert compose_shared_context_block(None) == ""

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert compose_shared_context_block(tmp_path / "nope") == ""

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "shared").mkdir()
        assert compose_shared_context_block(tmp_path / "shared") == ""

    def test_single_file_block(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        (shared / "spec.md").write_text("hello\n", encoding="utf-8")

        block = compose_shared_context_block(shared)
        assert "<shared-context>" in block
        assert "</shared-context>" in block
        assert 'name="spec.md"' in block
        # sha256 hash of "hello\n"
        assert hashlib.sha256(b"hello\n").hexdigest() in block
        assert "hello" in block

    def test_multiple_files_are_sorted(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        (shared / "b.md").write_text("B", encoding="utf-8")
        (shared / "a.md").write_text("A", encoding="utf-8")

        block = compose_shared_context_block(shared)
        # "a.md" must appear before "b.md" — deterministic ordering
        # keeps prompt caches stable.
        assert block.index('name="a.md"') < block.index('name="b.md"')

    def test_skips_dotfiles(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        (shared / ".hidden").write_text("secret", encoding="utf-8")
        (shared / "visible.md").write_text("v", encoding="utf-8")

        block = compose_shared_context_block(shared)
        assert ".hidden" not in block
        assert "visible.md" in block

    def test_unicode_content_round_trips(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        (shared / "한글.md").write_text("안녕하세요\n", encoding="utf-8")

        block = compose_shared_context_block(shared)
        assert "안녕하세요" in block
        assert "한글.md" in block

    def test_non_utf8_file_is_skipped_not_crashed(
        self, tmp_path: Path
    ) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        # Deliberate invalid UTF-8 — feature surface says server should
        # reject these, but the prompt composer must be defensive.
        (shared / "bad.bin").write_bytes(b"\xff\xfe\x00")
        (shared / "good.md").write_text("ok", encoding="utf-8")

        block = compose_shared_context_block(shared)
        assert "good.md" in block
        # Bad file is silently skipped rather than raising.
        assert "bad.bin" not in block
