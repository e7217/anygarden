"""Tests for ``doorae.rooms.file_storage`` (#246)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from doorae.rooms.file_storage import (
    FileTooLargeError,
    StoredFile,
    cleanup_orphans,
    delete_file,
    read_file,
    save_upload,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestSaveUpload:
    def test_writes_payload_and_returns_metadata(self, tmp_path: Path) -> None:
        payload = b"hello doorae\n"
        result = save_upload(
            room_files_dir=tmp_path,
            room_id="room-a",
            file_id="file-1",
            stream=io.BytesIO(payload),
            max_size_bytes=1024,
        )
        assert isinstance(result, StoredFile)
        assert result.storage_path == "room-a/file-1"
        assert result.size_bytes == len(payload)
        assert result.sha256 == _sha256(payload)

        final = tmp_path / "room-a" / "file-1"
        assert final.exists()
        assert final.read_bytes() == payload

    def test_tmp_dir_empty_after_rename(self, tmp_path: Path) -> None:
        save_upload(
            room_files_dir=tmp_path,
            room_id="room-a",
            file_id="file-1",
            stream=io.BytesIO(b"x"),
            max_size_bytes=1024,
        )
        tmp_dir = tmp_path / "room-a" / ".tmp"
        # .tmp may or may not exist, but if it does, it must be empty.
        if tmp_dir.exists():
            assert list(tmp_dir.iterdir()) == []

    def test_exact_size_limit_is_ok(self, tmp_path: Path) -> None:
        payload = b"0123456789"
        result = save_upload(
            room_files_dir=tmp_path,
            room_id="room-a",
            file_id="file-1",
            stream=io.BytesIO(payload),
            max_size_bytes=len(payload),
        )
        assert result.size_bytes == len(payload)

    def test_over_limit_raises_and_cleans_tmp(self, tmp_path: Path) -> None:
        payload = b"0123456789AB"  # 12 bytes
        with pytest.raises(FileTooLargeError):
            save_upload(
                room_files_dir=tmp_path,
                room_id="room-a",
                file_id="file-1",
                stream=io.BytesIO(payload),
                max_size_bytes=10,
            )
        # No final file.
        assert not (tmp_path / "room-a" / "file-1").exists()
        # And no leftover temp file either.
        tmp_dir = tmp_path / "room-a" / ".tmp"
        if tmp_dir.exists():
            assert list(tmp_dir.iterdir()) == []

    def test_creates_nested_directories(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "root"
        save_upload(
            room_files_dir=nested,
            room_id="room-a",
            file_id="file-1",
            stream=io.BytesIO(b"x"),
            max_size_bytes=16,
        )
        assert (nested / "room-a" / "file-1").exists()


class TestDeleteFile:
    def test_removes_existing_file(self, tmp_path: Path) -> None:
        save_upload(
            room_files_dir=tmp_path,
            room_id="room-a",
            file_id="file-1",
            stream=io.BytesIO(b"x"),
            max_size_bytes=16,
        )
        delete_file(tmp_path, "room-a/file-1")
        assert not (tmp_path / "room-a" / "file-1").exists()

    def test_missing_file_is_noop(self, tmp_path: Path) -> None:
        # Must not raise.
        delete_file(tmp_path, "room-a/nope")


class TestReadFile:
    def test_reads_utf8(self, tmp_path: Path) -> None:
        payload = "안녕하세요\nhello".encode("utf-8")
        save_upload(
            room_files_dir=tmp_path,
            room_id="room-a",
            file_id="file-1",
            stream=io.BytesIO(payload),
            max_size_bytes=1024,
        )
        assert read_file(tmp_path, "room-a/file-1") == payload.decode("utf-8")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_file(tmp_path, "room-a/nope")


class TestCleanupOrphans:
    def test_keeps_known_and_removes_orphans(self, tmp_path: Path) -> None:
        for fid in ("keep-1", "orphan-1", "orphan-2"):
            save_upload(
                room_files_dir=tmp_path,
                room_id="room-a",
                file_id=fid,
                stream=io.BytesIO(fid.encode()),
                max_size_bytes=1024,
            )

        removed = cleanup_orphans(tmp_path, known_ids={"keep-1"})
        assert removed == 2
        assert (tmp_path / "room-a" / "keep-1").exists()
        assert not (tmp_path / "room-a" / "orphan-1").exists()
        assert not (tmp_path / "room-a" / "orphan-2").exists()

    def test_sweeps_leftover_tmp(self, tmp_path: Path) -> None:
        # Simulate a crash that left a half-written temp file behind.
        tmp_dir = tmp_path / "room-a" / ".tmp"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "half").write_bytes(b"partial")

        removed = cleanup_orphans(tmp_path, known_ids=set())
        assert removed >= 1
        assert not (tmp_dir / "half").exists()

    def test_empty_root_is_noop(self, tmp_path: Path) -> None:
        # Missing root dir must not crash.
        assert cleanup_orphans(tmp_path / "missing", known_ids=set()) == 0

    def test_multiple_rooms(self, tmp_path: Path) -> None:
        for room, fid in [("room-a", "keep"), ("room-b", "drop")]:
            save_upload(
                room_files_dir=tmp_path,
                room_id=room,
                file_id=fid,
                stream=io.BytesIO(b"x"),
                max_size_bytes=16,
            )
        cleanup_orphans(tmp_path, known_ids={"keep"})
        assert (tmp_path / "room-a" / "keep").exists()
        assert not (tmp_path / "room-b" / "drop").exists()
