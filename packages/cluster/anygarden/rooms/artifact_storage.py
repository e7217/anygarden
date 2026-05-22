"""Disk-backed storage for room artifacts (#290 Phase B).

Artifact bytes live under ``<artifact_files_dir>/<room_id>/<file_id>``.
The DB only keeps metadata + sha256 — same separation as
``file_storage.py``, but artifacts are binary (image/png is the
headline use case) so we operate on bytes rather than UTF-8 text.

Atomic write contract
---------------------
``save_bytes`` streams into ``<room_dir>/.tmp/<file_id>`` first, then
``os.replace`` to the final path. Caller commits the DB row *after*
this returns; on commit failure the caller MUST call ``delete_file``
to avoid an orphaned blob.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


_TMP_SUBDIR = ".tmp"


class FileTooLargeError(Exception):
    """Raised when ``data`` exceeds the configured size ceiling."""


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """Outcome of a successful ``save_bytes``.

    ``storage_path`` is relative to ``artifact_files_dir`` (e.g.
    ``"<room_id>/<file_id>"``) so the same value works across
    deployments where the root differs.
    """

    storage_path: str
    sha256: str
    size_bytes: int


def save_bytes(
    *,
    artifact_files_dir: Path,
    room_id: str,
    file_id: str,
    data: bytes,
    max_size_bytes: int,
) -> StoredArtifact:
    """Atomically persist *data* under
    ``<artifact_files_dir>/<room_id>/<file_id>``.

    Raises :class:`FileTooLargeError` if ``len(data) > max_size_bytes``.
    The temp file is unlinked before the exception propagates.
    """
    size = len(data)
    if size > max_size_bytes:
        raise FileTooLargeError(
            f"artifact exceeded {max_size_bytes} bytes (got {size})"
        )
    room_dir = artifact_files_dir / room_id
    tmp_dir = room_dir / _TMP_SUBDIR
    tmp_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = tmp_dir / file_id
    final_path = room_dir / file_id
    storage_path = f"{room_id}/{file_id}"

    try:
        tmp_path.write_bytes(data)
        os.replace(tmp_path, final_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    return StoredArtifact(
        storage_path=storage_path,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=size,
    )


def delete_file(artifact_files_dir: Path, storage_path: str) -> None:
    """Unlink a previously stored artifact. No-op if already gone."""
    (artifact_files_dir / storage_path).unlink(missing_ok=True)


def read_bytes(artifact_files_dir: Path, storage_path: str) -> bytes:
    """Load a stored artifact as bytes.

    Raises ``FileNotFoundError`` if the file vanished between the DB
    lookup and this call — callers should treat that as a data
    integrity event worth logging.
    """
    return (artifact_files_dir / storage_path).read_bytes()


def cleanup_orphans(artifact_files_dir: Path, known_ids: set[str]) -> int:
    """Remove on-disk artifacts whose ``file_id`` isn't in
    ``known_ids``. Also sweeps any leftovers under ``<room>/.tmp/``.
    Returns the count removed.
    """
    if not artifact_files_dir.exists():
        return 0

    removed = 0
    for room_dir in artifact_files_dir.iterdir():
        if not room_dir.is_dir():
            continue
        tmp_dir = room_dir / _TMP_SUBDIR
        if tmp_dir.exists():
            for leftover in tmp_dir.iterdir():
                if leftover.is_file():
                    leftover.unlink()
                    removed += 1
        for entry in room_dir.iterdir():
            if entry.name == _TMP_SUBDIR:
                continue
            if entry.is_file() and entry.name not in known_ids:
                entry.unlink()
                removed += 1
    return removed
