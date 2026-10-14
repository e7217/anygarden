"""Tests for ``compose_shared_context_block`` (#246)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from anygarden.agent.memory import compose_shared_context_block


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


@pytest.mark.req("MEM-05", "WS-06")
class TestRoomScopedSharedContext:
    def test_reads_only_the_current_rooms_directory(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        (shared / "room-a").mkdir(parents=True)
        (shared / "room-b").mkdir(parents=True)
        (shared / "room-a" / "a.md").write_text("ROOM-A", encoding="utf-8")
        (shared / "room-b" / "b.md").write_text("ROOM-B", encoding="utf-8")
        (shared / "legacy.md").write_text("LEGACY-FLAT", encoding="utf-8")

        block = compose_shared_context_block(shared, room_id="room-a")
        assert "ROOM-A" in block
        assert "ROOM-B" not in block
        assert "LEGACY-FLAT" not in block

    def test_path_like_room_ids_read_nothing(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        (shared / "room-b").mkdir(parents=True)
        (shared / "room-b" / "b.md").write_text("ROOM-B", encoding="utf-8")
        for bad in ("..", ".", "", "room-a/../room-b"):
            assert compose_shared_context_block(shared, room_id=bad) == ""

    def test_symlinked_file_is_skipped(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.md"
        outside.write_text("OUTSIDE-SECRET", encoding="utf-8")
        room = tmp_path / "shared" / "room-a"
        room.mkdir(parents=True)
        (room / "ok.md").write_text("ok", encoding="utf-8")
        (room / "link.md").symlink_to(outside)

        block = compose_shared_context_block(tmp_path / "shared", room_id="room-a")
        assert "ok.md" in block
        assert "link.md" not in block
        assert "OUTSIDE-SECRET" not in block

    def test_symlinked_room_directory_is_ignored(self, tmp_path: Path) -> None:
        sibling = tmp_path / "shared" / "room-b"
        sibling.mkdir(parents=True)
        (sibling / "b.md").write_text("ROOM-B", encoding="utf-8")
        (tmp_path / "shared" / "room-a").symlink_to(sibling, target_is_directory=True)

        assert compose_shared_context_block(tmp_path / "shared", room_id="room-a") == ""
