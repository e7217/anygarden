"""Attach files an agent wrote during a turn to that turn's reply (#854).

The agent drops files under ``memory/outbox/<room_id>/``. The supervisor
snapshots that directory before the engine runs and, once the turn has a
reply, uploads whatever is new or changed and references it from the reply's
``metadata.references``. Only regular files directly inside the room
directory count — the same shape the daemon's outbox poller accepts.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from anygarden.agent.client import ArtifactUploadError

log = structlog.get_logger(__name__)

# Keeps one reply readable; extra files stay in the room's artifact panel.
REPLY_ATTACHMENT_LIMIT = 10

_UPLOAD_FAILURE_REASONS = {413: "too_large", 415: "unsupported_type"}


def _room_dir(outbox_dir: Path | None, room_id: str) -> Path | None:
    if outbox_dir is None:
        return None
    try:
        if str(UUID(room_id)) != room_id:
            return None
    except ValueError:
        return None
    room_dir = outbox_dir / room_id
    if not room_dir.is_dir() or room_dir.is_symlink():
        return None
    return room_dir


def _regular_files(room_dir: Path) -> list[Path]:
    return [
        entry
        for entry in sorted(room_dir.iterdir())
        if entry.is_file() and not entry.is_symlink()
    ]


def snapshot_outbox(outbox_dir: Path | None, room_id: str) -> dict[str, str]:
    """Return ``{filename: sha256}`` for the room's outbox files."""
    room_dir = _room_dir(outbox_dir, room_id)
    if room_dir is None:
        return {}
    snapshot: dict[str, str] = {}
    for entry in _regular_files(room_dir):
        try:
            snapshot[entry.name] = hashlib.sha256(entry.read_bytes()).hexdigest()
        except OSError:
            continue
    return snapshot


def changed_outbox_files(
    outbox_dir: Path | None, room_id: str, before: dict[str, str]
) -> list[Path]:
    """Files that are new or whose bytes changed since *before*."""
    after = snapshot_outbox(outbox_dir, room_id)
    room_dir = _room_dir(outbox_dir, room_id)
    if room_dir is None:
        return []
    return [room_dir / name for name, digest in after.items() if before.get(name) != digest]


async def upload_reply_attachments(
    client: Any,
    room_id: str,
    paths: list[Path],
    *,
    limit: int = REPLY_ATTACHMENT_LIMIT,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Upload *paths* and return ``(references, undelivered)``.

    A failed upload never blocks the reply; it is reported back as an
    ``undelivered`` entry so the user can see what is missing.
    """
    references: list[dict[str, str]] = []
    undelivered: list[dict[str, str]] = []
    for index, path in enumerate(paths):
        if index >= limit:
            undelivered.append({"name": path.name, "reason": "too_many"})
            continue
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            row = await client.upload_room_artifact(room_id, path, mime)
        except (ArtifactUploadError, OSError) as exc:
            status = getattr(exc, "status", None)
            reason = _UPLOAD_FAILURE_REASONS.get(status, "upload_failed")
            log.warning(
                "reply_attachment_upload_failed",
                room_id=room_id,
                filename=path.name,
                status=status,
            )
            undelivered.append({"name": path.name, "reason": reason})
            continue
        references.append({"type": "artifact", "id": row["id"]})
    return references, undelivered
