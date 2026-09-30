"""Backend-agnostic helpers used by both POSIX and Windows safefs."""

from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def normalise(path: PathLike) -> Path:
    """Convert *path* to an absolute ``Path`` without resolving symlinks.

    ``Path.resolve()`` would silently follow links, defeating the
    purpose of the symlink-refusing writers. ``absolute()`` keeps the
    final component intact so the platform-specific ``open`` call can
    decide whether to follow it.
    """
    return Path(path).absolute()
