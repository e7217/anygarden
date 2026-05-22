"""Unit tests for ``anygarden_machine.safefs`` helpers.

The helpers centralise the ``O_NOFOLLOW`` contract: file writes that
land inside the agent directory must refuse to follow symlinks at the
final path component so a tampered symlink left by a prior session
cannot redirect the write to a root-owned file outside the agent root.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from anygarden_machine.safefs import safe_write_bytes, safe_write_text, secure_chmod


class TestSafeWriteText:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "hello.txt"
        safe_write_text(target, "hi", mode=0o600)
        assert target.read_text() == "hi"
        assert (target.stat().st_mode & 0o777) == 0o600

    def test_overwrites_regular_file(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("old")
        safe_write_text(target, "new", mode=0o600)
        assert target.read_text() == "new"

    def test_refuses_symlink_target(self, tmp_path: Path) -> None:
        """If the target path is already a symlink, the write must fail
        instead of following to the symlink's destination."""
        victim = tmp_path / "victim.txt"
        victim.write_text("untouched")
        trap = tmp_path / "trap.txt"
        trap.symlink_to(victim)

        with pytest.raises(OSError):
            safe_write_text(trap, "attacker", mode=0o600)

        # victim must not have been overwritten through the symlink
        assert victim.read_text() == "untouched"

    def test_honours_custom_mode(self, tmp_path: Path) -> None:
        target = tmp_path / "ro.txt"
        safe_write_text(target, "locked", mode=0o400)
        assert (target.stat().st_mode & 0o777) == 0o400

    def test_writes_utf8_bytes(self, tmp_path: Path) -> None:
        target = tmp_path / "u.txt"
        safe_write_text(target, "héllo ☃", mode=0o600)
        assert target.read_text(encoding="utf-8") == "héllo ☃"


class TestSafeWriteBytes:
    def test_creates_new_file_with_bytes(self, tmp_path: Path) -> None:
        target = tmp_path / "b.bin"
        safe_write_bytes(target, b"\x00\x01", mode=0o600)
        assert target.read_bytes() == b"\x00\x01"

    def test_refuses_symlink_target(self, tmp_path: Path) -> None:
        victim = tmp_path / "v.bin"
        victim.write_bytes(b"ok")
        trap = tmp_path / "t.bin"
        trap.symlink_to(victim)

        with pytest.raises(OSError):
            safe_write_bytes(trap, b"bad", mode=0o600)

        assert victim.read_bytes() == b"ok"


class TestODanglingSymlinks:
    """A dangling symlink (target doesn't exist) is still a symlink —
    write must refuse it. This matters because a malicious agent could
    symlink to a non-existent path to avoid detection via ``exists()``.
    """

    def test_dangling_symlink_refused(self, tmp_path: Path) -> None:
        trap = tmp_path / "dangle"
        trap.symlink_to(tmp_path / "does-not-exist")

        with pytest.raises(OSError):
            safe_write_text(trap, "x", mode=0o600)

        # Ensure no new file was created at the symlink target either.
        assert not (tmp_path / "does-not-exist").exists()


class TestParentDirUnaffected:
    """O_NOFOLLOW only guards the final component. Parent-directory
    symlinks are out of scope here — documented limitation."""

    def test_final_component_only(self, tmp_path: Path) -> None:
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        # Writing through a symlinked parent dir succeeds because the
        # final-component check doesn't fire on parents.
        target = link_dir / "file.txt"
        safe_write_text(target, "ok", mode=0o600)

        # The write landed in the real dir (followed parent symlink).
        assert (real_dir / "file.txt").read_text() == "ok"


class TestSecureChmod:
    """``secure_chmod`` is the cross-platform replacement for
    ``os.chmod``. On POSIX it pins the exact mode bits. On Windows it
    applies an owner-only DACL (covered by Windows-only tests)."""

    def test_pins_owner_only_mode(self, tmp_path: Path) -> None:
        target = tmp_path / "secret"
        target.write_text("x")
        secure_chmod(target, 0o600)
        assert (target.stat().st_mode & 0o777) == 0o600

    def test_pins_directory_mode(self, tmp_path: Path) -> None:
        d = tmp_path / "private"
        d.mkdir()
        secure_chmod(d, 0o700)
        assert (d.stat().st_mode & 0o777) == 0o700

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        target = tmp_path / "f"
        target.write_text("x")
        secure_chmod(str(target), 0o600)
        assert (target.stat().st_mode & 0o777) == 0o600
