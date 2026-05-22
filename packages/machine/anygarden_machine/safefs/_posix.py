"""POSIX (Linux + macOS) backend for ``safefs``.

Uses ``O_NOFOLLOW`` to atomically refuse a symlink at the final path
component. ``O_NOFOLLOW`` is a POSIX standard flag; if the final
component is a symlink, ``open`` fails with ``ELOOP``. Callers that
want to replace an existing symlink must ``lstat``-check and
``unlink`` it first — this module only refuses follow, it does not
silently overwrite.

Limitation: ``O_NOFOLLOW`` only guards the final component. A symlink
in any parent directory is still traversed. The agent root's parent
structure is owned by the daemon process and is not writable by the
agent sandbox, so parent-dir tamper is out of the threat model here.
"""

from __future__ import annotations

import os
from pathlib import Path

from ._common import PathLike, normalise


def _write_fd(path: Path, data: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(str(path), flags, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    secure_chmod(path, mode)


def safe_write_text(path: PathLike, content: str, *, mode: int = 0o600) -> None:
    """Write *content* (UTF-8) to *path*, refusing to follow symlinks.

    Raises ``OSError`` (with errno ``ELOOP``) if *path* exists as a
    symlink at the final component.
    """
    _write_fd(normalise(path), content.encode("utf-8"), mode)


def safe_write_bytes(path: PathLike, content: bytes, *, mode: int = 0o600) -> None:
    """Write raw *content* to *path*, refusing to follow symlinks.

    Raises ``OSError`` (with errno ``ELOOP``) if *path* exists as a
    symlink at the final component.
    """
    _write_fd(normalise(path), content, mode)


def secure_chmod(path: PathLike, mode: int) -> None:
    """Pin file/directory permissions on POSIX.

    Thin wrapper over ``os.chmod`` — provided so callers use one
    cross-platform helper rather than ``os.chmod`` directly. On
    Windows, the corresponding helper applies an ACL DACL instead.
    """
    os.chmod(str(path), mode)
