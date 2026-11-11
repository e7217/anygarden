"""#1018 — remove the files of rooms archived longer than the retention period.

Archiving (and deleting, #911) keeps a room readable and reversible, so its
files would otherwise stay on disk forever. Once a room has been archived
for ``retention_days``:

- agent artifacts lose their bytes and keep a tombstone row (``purged_at``),
  so a link to one answers "expired" rather than "not found", and the same
  bytes uploaded again after an unarchive bring the row (and old links) back;
- shared files a person uploaded are deleted like a manual delete — they
  are agent input, never linked from a message, and agents drop their
  copies through the usual delete fan-out.

The blob goes first and the row is marked after, so a crash in between is
repaired by the next sweep (deleting a missing file is a no-op).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Room, RoomArtifact, RoomSharedFile
from anygarden.rooms import artifact_storage, file_storage

DEFAULT_BATCH_SIZE = 500


@dataclass
class RetentionSweepResult:
    artifacts: int = 0
    artifact_bytes: int = 0
    # ``(room_id, storage_name)`` of each deleted shared file, for the
    # caller's delete fan-out to the agents holding a copy.
    shared_files: list[tuple[str, str]] = field(default_factory=list)
    shared_file_bytes: int = 0
    rooms: set[str] = field(default_factory=set)


def _expired_rooms(cutoff: datetime):
    return select(Room.id).where(Room.archived_at.is_not(None), Room.archived_at <= cutoff)


async def sweep_archived_room_files(
    db: AsyncSession,
    *,
    artifact_files_dir: Path,
    room_files_dir: Path,
    retention_days: int,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> RetentionSweepResult:
    """Remove up to *batch_size* artifacts and shared files of rooms
    archived at least *retention_days* ago. ``0`` disables the sweep."""
    result = RetentionSweepResult()
    if retention_days <= 0:
        return result
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)

    artifacts = list(await db.scalars(
        select(RoomArtifact)
        .where(RoomArtifact.room_id.in_(_expired_rooms(cutoff)), RoomArtifact.purged_at.is_(None))
        .order_by(RoomArtifact.created_at)
        .limit(batch_size)
    ))
    for row in artifacts:
        artifact_storage.delete_file(artifact_files_dir, row.storage_path)
    if artifacts:
        marked = await db.execute(
            update(RoomArtifact)
            .where(
                RoomArtifact.id.in_([row.id for row in artifacts]),
                # A room unarchived meanwhile is not marked; its link then
                # reports missing bytes like any other lost file.
                RoomArtifact.room_id.in_(_expired_rooms(cutoff)),
            )
            .values(purged_at=now or datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        result.artifacts = marked.rowcount or 0
        result.artifact_bytes = sum(row.size_bytes for row in artifacts)
        result.rooms.update(row.room_id for row in artifacts)

    shared = list(await db.scalars(
        select(RoomSharedFile)
        .where(RoomSharedFile.room_id.in_(_expired_rooms(cutoff)))
        .order_by(RoomSharedFile.created_at)
        .limit(batch_size)
    ))
    for row in shared:
        await db.delete(row)
    if shared:
        await db.commit()
    for row in shared:
        file_storage.delete_file(room_files_dir, row.storage_path)
        result.shared_files.append((row.room_id, row.storage_name))
        result.shared_file_bytes += row.size_bytes
        result.rooms.add(row.room_id)
    return result
