"""Disk-backed storage for room shared files (#246).

Files live under ``<room_files_dir>/<room_id>/<file_id>``. The DB only
keeps metadata + sha256; the raw bytes sit on disk so the default
SQLite ``anygarden.db`` stays compact as rooms accumulate attachments.

Atomic write contract
---------------------
Uploads stream into ``<room_files_dir>/<room_id>/.tmp/<file_id>``
first, compute sha256 along the way, then ``os.rename`` to the final
path. The caller is responsible for committing the DB row *after*
``save_upload`` returns successfully; on commit failure the caller
MUST call ``delete_file`` to avoid an orphaned file.

All public helpers take ``room_files_dir`` explicitly (rather than
reading ``AnygardenSettings``) so the module is trivial to unit-test
against a ``tmp_path`` fixture.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


_TMP_SUBDIR = ".tmp"
_CHUNK_SIZE = 64 * 1024


class FileTooLargeError(Exception):
    """Upload stream exceeded the configured size ceiling mid-transfer.

    When raised, the partial temp file has already been unlinked by
    ``save_upload`` — the caller just propagates the 413 to the client.
    """


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Outcome of a successful ``save_upload``.

    ``storage_path`` is relative to ``room_files_dir`` (e.g.
    ``"<room_id>/<file_id>"``) so the same value works across
    deployments where the root directory differs.
    """

    storage_path: str
    sha256: str
    size_bytes: int


def save_upload(
    *,
    room_files_dir: Path,
    room_id: str,
    file_id: str,
    stream: BinaryIO,
    max_size_bytes: int,
) -> StoredFile:
    """Atomically persist an upload to disk.

    Streams bytes from ``stream`` into a temp file under
    ``<room_files_dir>/<room_id>/<_TMP_SUBDIR>/<file_id>``, computing
    sha256 as it goes. On success the temp file is ``os.rename``-d to
    the final path ``<room_files_dir>/<room_id>/<file_id>`` and a
    ``StoredFile`` is returned.

    Raises ``FileTooLargeError`` if the stream emits more than
    ``max_size_bytes``; the partial temp file is removed before the
    exception propagates.
    """
    room_dir = room_files_dir / room_id
    tmp_dir = room_dir / _TMP_SUBDIR
    tmp_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = tmp_dir / file_id
    final_path = room_dir / file_id
    storage_path = f"{room_id}/{file_id}"

    hasher = hashlib.sha256()
    size = 0
    try:
        with tmp_path.open("wb") as dst:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size_bytes:
                    raise FileTooLargeError(
                        f"upload exceeded {max_size_bytes} bytes"
                    )
                hasher.update(chunk)
                dst.write(chunk)
        os.replace(tmp_path, final_path)
    except BaseException:
        # Includes FileTooLargeError plus any I/O or cancellation —
        # the partial temp file must not linger regardless of cause.
        tmp_path.unlink(missing_ok=True)
        raise

    return StoredFile(
        storage_path=storage_path,
        sha256=hasher.hexdigest(),
        size_bytes=size,
    )


def delete_file(room_files_dir: Path, storage_path: str) -> None:
    """Unlink a previously stored file. No-op if it's already gone.

    Used both in the normal delete path (room admin removes a file)
    and in the rollback path when a DB commit fails after
    ``save_upload`` succeeded.
    """
    (room_files_dir / storage_path).unlink(missing_ok=True)


def read_file(room_files_dir: Path, storage_path: str) -> str:
    """Load a stored file as UTF-8 text.

    Shared files are text-mime only (see the endpoint's MIME
    whitelist), so strict UTF-8 decoding is the right contract.
    Raises ``FileNotFoundError`` if the file vanished between the DB
    lookup and this call — callers should treat that as a data
    integrity event worth logging.
    """
    return (room_files_dir / storage_path).read_text(encoding="utf-8")


def cleanup_orphans(room_files_dir: Path, known_ids: set[str]) -> int:
    """Remove on-disk files whose ``file_id`` isn't in ``known_ids``.

    Also sweeps any leftover entries under ``<room_id>/.tmp/`` — those
    are always reclaimable because a successful upload renames out of
    the temp subdir before the DB commits. Returns the count of
    removed files. Intended for server startup, to reconcile crashes
    that left the filesystem ahead of the DB.
    """
    if not room_files_dir.exists():
        return 0

    removed = 0
    for room_dir in room_files_dir.iterdir():
        if not room_dir.is_dir():
            continue
        # Sweep the whole ``.tmp/`` subdir unconditionally — nothing
        # there is authoritative once the process has come back up.
        tmp_dir = room_dir / _TMP_SUBDIR
        if tmp_dir.exists():
            for leftover in tmp_dir.iterdir():
                if leftover.is_file():
                    leftover.unlink()
                    removed += 1
        # Remove room-level entries whose ``file_id`` is unknown.
        for entry in room_dir.iterdir():
            if entry.name == _TMP_SUBDIR:
                continue
            if entry.is_file() and entry.name not in known_ids:
                entry.unlink()
                removed += 1
    return removed
