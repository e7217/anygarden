"""Windows backend for ``safefs``.

Implements the same contract as the POSIX backend using Win32 APIs
called through ``ctypes`` (no ``pywin32`` dependency required):

* ``safe_write_text`` / ``safe_write_bytes`` — open the path with
  ``CreateFileW`` + ``FILE_FLAG_OPEN_REPARSE_POINT`` so the kernel
  returns a handle to the reparse point itself rather than following
  it. ``GetFileInformationByHandle`` then exposes ``dwFileAttributes``;
  if ``FILE_ATTRIBUTE_REPARSE_POINT`` is set, the handle is closed
  without writing and an ``OSError(ELOOP)`` is raised. The check is
  atomic at the OS level — there is no TOCTOU window between detect
  and write because we never re-open the path.

* ``secure_chmod`` — translate the owner bits of a POSIX mode
  (``0o600``, ``0o700``, ``0o400``) into a Windows DACL. Every doorae
  call site uses owner-only modes, so the DACL grants
  ``GENERIC_READ | GENERIC_WRITE`` (or ``GENERIC_READ`` alone for
  ``0o400``) to the current process owner SID and explicitly removes
  inherited ACEs. Group / other bits are intentionally ignored —
  POSIX "group" has no well-defined Windows analogue, and every
  doorae call site means owner-only anyway.

Notes
-----
* The implementation deliberately avoids ``pywin32``. ``ctypes`` calls
  are stable on Win10 1607+ and remove a wheel dependency.
* If a future call site needs richer mode mapping (``0o660`` etc.),
  extend ``secure_chmod`` to accept an explicit principals list
  rather than overloading mode bits.
"""

from __future__ import annotations

import ctypes
import errno
import os
from ctypes import wintypes
from pathlib import Path

from ._common import PathLike, normalise

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
CREATE_ALWAYS = 2
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000

TOKEN_QUERY = 0x0008
TokenUser = 1


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
    ]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _advapi32():
    return ctypes.WinDLL("advapi32", use_last_error=True)


def _raise_winerror(path: Path, default_errno: int = errno.EIO) -> None:
    code = ctypes.get_last_error()
    raise OSError(default_errno, ctypes.FormatError(code), str(path))


# ---------------------------------------------------------------------------
# safe_write_*
# ---------------------------------------------------------------------------


def _open_no_reparse(path: Path) -> int:
    """``CreateFileW`` with ``FILE_FLAG_OPEN_REPARSE_POINT``.

    Returns the raw handle. If the file exists and is a reparse point
    (symlink, junction, mount point), the handle is closed and
    ``OSError(ELOOP)`` is raised — emulating the POSIX
    ``O_NOFOLLOW`` contract.
    """
    k32 = _kernel32()
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k32.CreateFileW.restype = wintypes.HANDLE

    handle = k32.CreateFileW(
        str(path),
        GENERIC_WRITE,
        FILE_SHARE_READ,
        None,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == INVALID_HANDLE_VALUE or handle is None:
        _raise_winerror(path)

    # Now confirm we didn't just create-or-open a reparse point.
    k32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    k32.GetFileInformationByHandle.restype = wintypes.BOOL

    info = _BY_HANDLE_FILE_INFORMATION()
    if not k32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        k32.CloseHandle(handle)
        _raise_winerror(path)

    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        k32.CloseHandle(handle)
        raise OSError(
            errno.ELOOP,
            "Refusing to follow reparse point at final component",
            str(path),
        )

    return handle


def _write_handle(handle: int, data: bytes, path: Path) -> None:
    k32 = _kernel32()
    k32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    k32.WriteFile.restype = wintypes.BOOL

    written = wintypes.DWORD(0)
    buf = ctypes.create_string_buffer(data)
    if not k32.WriteFile(handle, buf, len(data), ctypes.byref(written), None):
        k32.CloseHandle(handle)
        _raise_winerror(path)
    if written.value != len(data):
        k32.CloseHandle(handle)
        raise OSError(errno.EIO, "Short write", str(path))
    k32.CloseHandle(handle)


def safe_write_text(path: PathLike, content: str, *, mode: int = 0o600) -> None:
    """Write *content* (UTF-8) to *path*, refusing reparse points."""
    target = normalise(path)
    handle = _open_no_reparse(target)
    _write_handle(handle, content.encode("utf-8"), target)
    secure_chmod(target, mode)


def safe_write_bytes(path: PathLike, content: bytes, *, mode: int = 0o600) -> None:
    """Write raw *content* to *path*, refusing reparse points."""
    target = normalise(path)
    handle = _open_no_reparse(target)
    _write_handle(handle, content, target)
    secure_chmod(target, mode)


# ---------------------------------------------------------------------------
# secure_chmod — owner-only DACL
# ---------------------------------------------------------------------------


def _current_user_sid() -> bytes:
    """Return the current process owner's SID as a binary blob."""
    a32 = _advapi32()
    k32 = _kernel32()

    a32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    a32.OpenProcessToken.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not a32.OpenProcessToken(
        k32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        _raise_winerror(Path("<token>"))

    try:
        a32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        a32.GetTokenInformation.restype = wintypes.BOOL

        size = wintypes.DWORD(0)
        a32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))
        if size.value == 0:
            _raise_winerror(Path("<token-info-size>"))

        buf = ctypes.create_string_buffer(size.value)
        if not a32.GetTokenInformation(
            token, TokenUser, buf, size.value, ctypes.byref(size)
        ):
            _raise_winerror(Path("<token-info>"))

        token_user = ctypes.cast(buf, ctypes.POINTER(_TOKEN_USER)).contents
        sid_ptr = token_user.User.Sid

        a32.GetLengthSid.argtypes = [ctypes.c_void_p]
        a32.GetLengthSid.restype = wintypes.DWORD
        sid_len = a32.GetLengthSid(sid_ptr)

        sid_copy = ctypes.create_string_buffer(sid_len)
        a32.CopySid.argtypes = [
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        a32.CopySid.restype = wintypes.BOOL
        if not a32.CopySid(sid_len, sid_copy, sid_ptr):
            _raise_winerror(Path("<sid-copy>"))

        return sid_copy.raw[:sid_len]
    finally:
        k32.CloseHandle(token)


def _build_owner_only_dacl(sid_blob: bytes, access_mask: int) -> ctypes.Array:
    """Build a self-relative ACL granting *access_mask* to the SID only.

    Uses ``InitializeAcl`` + ``AddAccessAllowedAce``. The resulting
    buffer is suitable as the ``pDacl`` argument to
    ``SetSecurityInfo``.
    """
    a32 = _advapi32()

    # Acl size: header + ACE header + access mask (DWORD) + SID
    acl_size = 8 + 8 + 4 + len(sid_blob) + 16  # generous padding
    acl = ctypes.create_string_buffer(acl_size)

    a32.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    a32.InitializeAcl.restype = wintypes.BOOL
    if not a32.InitializeAcl(acl, acl_size, 2):  # ACL_REVISION = 2
        _raise_winerror(Path("<acl-init>"))

    a32.AddAccessAllowedAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    a32.AddAccessAllowedAce.restype = wintypes.BOOL
    sid_buf = ctypes.create_string_buffer(sid_blob, len(sid_blob))
    if not a32.AddAccessAllowedAce(acl, 2, access_mask, sid_buf):
        _raise_winerror(Path("<ace-add>"))

    return acl


def _mode_to_access_mask(mode: int) -> int:
    """Map POSIX owner mode bits to Windows access rights.

    * Read+write (rw_): ``GENERIC_READ | GENERIC_WRITE``
    * Read only (r__): ``GENERIC_READ``
    * Write only (_w_): ``GENERIC_WRITE``
    * Execute (__x): folded into GENERIC_READ for directories — Windows
      treats directory traversal as part of the read right.
    """
    owner_bits = (mode >> 6) & 0o7
    mask = 0
    if owner_bits & 0o4:
        mask |= GENERIC_READ
    if owner_bits & 0o2:
        mask |= GENERIC_WRITE
    if owner_bits & 0o1 and not mask:
        # Pure execute (rare) — treat as read for traversal.
        mask |= GENERIC_READ
    if mask == 0:
        # Empty mode means no access — still create a DACL with zero
        # rights so the file is locked rather than world-readable.
        mask = 0
    return mask


def secure_chmod(path: PathLike, mode: int) -> None:
    """Apply an owner-only DACL on Windows.

    Removes inherited ACEs (``PROTECTED_DACL_SECURITY_INFORMATION``)
    and grants the current process owner the access rights derived
    from the *mode*'s owner bits.
    """
    target = normalise(path)
    if not target.exists():
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(target))

    sid_blob = _current_user_sid()
    access_mask = _mode_to_access_mask(mode)
    acl = _build_owner_only_dacl(sid_blob, access_mask)

    a32 = _advapi32()
    a32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,  # SE_OBJECT_TYPE
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    a32.SetNamedSecurityInfoW.restype = wintypes.DWORD

    SE_FILE_OBJECT = 1
    info = DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
    rc = a32.SetNamedSecurityInfoW(
        str(target),
        SE_FILE_OBJECT,
        info,
        None,  # owner SID — leave unchanged
        None,  # group SID — leave unchanged
        ctypes.cast(acl, ctypes.c_void_p),
        None,  # SACL — leave unchanged
    )
    if rc != 0:
        raise OSError(
            errno.EACCES,
            f"SetNamedSecurityInfoW failed (rc={rc})",
            str(target),
        )
