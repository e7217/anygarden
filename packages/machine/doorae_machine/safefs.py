"""Filesystem write helpers that refuse to follow symlinks.

Background
----------
``_materialize_agent_dir`` and ``ManifestStore`` previously used
``Path.write_text`` / ``Path.write_bytes`` to stage files under the
agent directory. Those methods open the path with the libc default,
which transparently follows a symlink at the final component and
writes to the link's resolved target. An agent that left a prepared
symlink in a prior session (or a buggy prune that missed one) could
therefore redirect a materialize-time write to a root-owned file
outside the agent root.

This module centralises the ``O_NOFOLLOW`` contract so every write
that lands inside an agent-controlled directory refuses to follow
symlinks at the final component. ``O_NOFOLLOW`` is a POSIX standard
flag (Linux + macOS); if the final component is a symlink, ``open``
fails with ``ELOOP``. Callers that want to replace an existing
symlink must ``lstat``-check and ``unlink`` it first — this module
only refuses follow, it does not silently overwrite.

Limitations
-----------
``O_NOFOLLOW`` only guards the final component of the path. A
symlink in any parent directory is still traversed. Full path
component-by-component resolution (``openat`` + ``O_NOFOLLOW`` at
every level) is substantially more complex and out of scope here;
the agent root's parent structure is owned by the daemon process
and is not writable by the agent sandbox, so parent-dir tamper is
out of the threat model for now.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union


def _write_fd(path: Path, data: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(str(path), flags, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    # ``os.open`` with ``mode`` honours the process umask, so an
    # explicit chmod is needed to pin the final permissions.
    os.chmod(str(path), mode)


def safe_write_text(
    path: Union[str, Path], content: str, *, mode: int = 0o600
) -> None:
    """Write *content* (UTF-8) to *path*, refusing to follow symlinks.

    Raises ``OSError`` (with errno ``ELOOP``) if *path* exists as a
    symlink at the final component.
    """
    _write_fd(Path(path), content.encode("utf-8"), mode)


def safe_write_bytes(
    path: Union[str, Path], content: bytes, *, mode: int = 0o600
) -> None:
    """Write raw *content* to *path*, refusing to follow symlinks.

    Raises ``OSError`` (with errno ``ELOOP``) if *path* exists as a
    symlink at the final component.
    """
    _write_fd(Path(path), content, mode)
