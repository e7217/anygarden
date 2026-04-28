"""Windows-only tests for the safefs Windows backend.

These tests are skipped on POSIX. On Windows runners (GitHub Actions
``windows-latest``) they verify:

* ``safe_write_text`` / ``safe_write_bytes`` reject reparse points
  (symbolic links and junctions) atomically.
* ``secure_chmod`` writes a DACL that strips inherited ACEs and
  grants only the current process owner the modeled rights.

The DACL assertions use ``GetNamedSecurityInfoW`` via ``ctypes`` so
the tests carry no extra dependency.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only safefs backend tests", allow_module_level=True)


from doorae_machine.safefs import safe_write_bytes, safe_write_text, secure_chmod  # noqa: E402


def _try_create_symlink(target: Path, source: Path) -> bool:
    """Create a symlink for testing if the dev mode / privilege allows.

    Windows requires either Administrator or Developer Mode to make
    symlinks. If neither is available, the test that needs it skips.
    """
    try:
        os.symlink(source, target)
        return True
    except (OSError, NotImplementedError):
        return False


class TestRejectsReparsePoints:
    def test_refuses_symlink_target(self, tmp_path: Path) -> None:
        victim = tmp_path / "victim.txt"
        victim.write_text("untouched")
        trap = tmp_path / "trap.txt"
        if not _try_create_symlink(trap, victim):
            pytest.skip("Symlink creation requires Developer Mode or admin")

        with pytest.raises(OSError):
            safe_write_text(trap, "attacker", mode=0o600)

        # victim must still be intact — atomic refusal, no partial write.
        assert victim.read_text() == "untouched"

    def test_refuses_dangling_symlink(self, tmp_path: Path) -> None:
        trap = tmp_path / "dangle"
        if not _try_create_symlink(trap, tmp_path / "does-not-exist"):
            pytest.skip("Symlink creation requires Developer Mode or admin")

        with pytest.raises(OSError):
            safe_write_text(trap, "x", mode=0o600)

        assert not (tmp_path / "does-not-exist").exists()


class TestPlainWritesWork:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        safe_write_text(target, "hello", mode=0o600)
        assert target.read_text() == "hello"

    def test_overwrites_regular_file(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("old")
        safe_write_text(target, "new", mode=0o600)
        assert target.read_text() == "new"

    def test_writes_bytes(self, tmp_path: Path) -> None:
        target = tmp_path / "b.bin"
        safe_write_bytes(target, b"\x00\x01\x02", mode=0o600)
        assert target.read_bytes() == b"\x00\x01\x02"


# ---------------------------------------------------------------------------
# DACL inspection helpers
# ---------------------------------------------------------------------------


def _read_dacl_ace_count(path: Path) -> int:
    """Return how many access-allowed ACEs are present in *path*'s DACL."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = ctypes.c_uint32

    DACL_SECURITY_INFORMATION = 0x4
    SE_FILE_OBJECT = 1

    pdacl = ctypes.c_void_p()
    psd = ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(
        str(path),
        SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(pdacl),
        None,
        ctypes.byref(psd),
    )
    if rc != 0:
        raise OSError(f"GetNamedSecurityInfoW rc={rc}")

    class _ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("AclSize", ctypes.c_uint16),
            ("AceCount", ctypes.c_uint16),
            ("Sbz2", ctypes.c_uint16),
        ]

    acl = ctypes.cast(pdacl, ctypes.POINTER(_ACL)).contents
    count = acl.AceCount

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    k32.LocalFree(psd)
    return count


class TestSecureChmodDacl:
    def test_owner_only_ace_after_chmod(self, tmp_path: Path) -> None:
        target = tmp_path / "secret"
        target.write_text("x")
        secure_chmod(target, 0o600)

        # The DACL should hold a single access-allowed ACE — the one
        # we added for the current user. Inherited ACEs from the
        # parent directory are explicitly stripped (PROTECTED_DACL).
        ace_count = _read_dacl_ace_count(target)
        assert ace_count == 1

    def test_directory_chmod(self, tmp_path: Path) -> None:
        d = tmp_path / "private"
        d.mkdir()
        secure_chmod(d, 0o700)
        assert _read_dacl_ace_count(d) == 1

    def test_chmod_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            secure_chmod(tmp_path / "no-such-file", 0o600)


class TestSecureChmodAllowsOwnerDelete:
    """Issue #304 regression: ``secure_chmod(0o600)`` previously emitted
    a DACL with ``GENERIC_READ | GENERIC_WRITE`` only. On Windows that
    omits ``DELETE``, so a subsequent ``unlink()`` failed with
    ``ERROR_ACCESS_DENIED`` — breaking spawner.materialize's prune step
    on every agent re-spawn. The fix maps any owner-write mode to
    ``FILE_ALL_ACCESS`` (POSIX "owner can delete" is implicit; Windows
    needs explicit ``DELETE``)."""

    def test_owner_can_unlink_file_after_chmod_600(self, tmp_path: Path) -> None:
        target = tmp_path / "manifest.json"
        target.write_text('{"k":"v"}')
        secure_chmod(target, 0o600)

        # Pre-fix: this raises PermissionError (WinError 5).
        target.unlink()
        assert not target.exists()

    def test_owner_can_rmdir_after_chmod_700(self, tmp_path: Path) -> None:
        d = tmp_path / "agent_dir"
        d.mkdir()
        secure_chmod(d, 0o700)

        d.rmdir()
        assert not d.exists()

    def test_owner_can_delete_child_in_chmod_700_dir(self, tmp_path: Path) -> None:
        d = tmp_path / "agent_dir"
        d.mkdir()
        secure_chmod(d, 0o700)
        child = d / "manifest.json"
        child.write_text("x")
        secure_chmod(child, 0o600)

        # Both file DELETE and parent FILE_DELETE_CHILD must be present
        # for the materialize prune step to succeed.
        child.unlink()
        assert not child.exists()

    def test_chmod_400_locks_writes(self, tmp_path: Path) -> None:
        """0o400 stays read-only — verify the mode bit logic didn't
        accidentally fall through to FILE_ALL_ACCESS for read-only."""
        target = tmp_path / "ro"
        target.write_text("locked")
        secure_chmod(target, 0o400)

        # Writing should fail because the DACL grants READ only.
        with pytest.raises(PermissionError):
            target.write_text("attempted overwrite")
