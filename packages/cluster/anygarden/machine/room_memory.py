"""Room note I/O that never follows directories or files through symlinks."""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

MAX_NOTES_BYTES = 262144


@contextmanager
def _room_directory(agent_root: Path, room_id: str, *, create: bool):
    if str(UUID(room_id)) != room_id:
        raise ValueError("noncanonical room memory scope")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(agent_root, flags)
    try:
        for component in ("memory", "rooms", room_id):
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read(fd: int, name: str = "notes.md") -> str:
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file_fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_NOTES_BYTES:
            raise ValueError("invalid room memory file")
        raw = stream.read(MAX_NOTES_BYTES + 1)
        if len(raw) > MAX_NOTES_BYTES:
            raise ValueError("room memory file too large")
        return raw.decode("utf-8")


def _write(fd: int, name: str, body: str):
    raw = body.encode("utf-8")
    if len(raw) > MAX_NOTES_BYTES:
        raise ValueError("room memory file too large")
    file_fd = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600, dir_fd=fd,
    )
    with os.fdopen(file_fd, "wb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("invalid room memory file")
        os.fchmod(stream.fileno(), 0o600)
        stream.truncate(0)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read_room_notes(agent_root: Path, room_id: str) -> str:
    with _room_directory(agent_root, room_id, create=False) as fd:
        return _read(fd)


def write_room_notes(agent_root: Path, room_id: str, body: str, *, preserve_current=False):
    """Preserve unacknowledged local edits before installing server authority."""
    with _room_directory(agent_root, room_id, create=True) as fd:
        if preserve_current:
            try:
                previous = _read(fd)
            except FileNotFoundError:
                previous = None
            if previous is not None and previous != body:
                digest = hashlib.sha256(previous.encode()).hexdigest()
                _write(fd, f"notes.unsynced.{digest}.md", previous)
        _write(fd, "notes.md", body)
