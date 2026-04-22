"""Room shared-file orchestration (#246).

Bridges the HTTP upload/list/delete endpoint, disk storage
(``file_storage``), the ``room_shared_files`` DB row, and the
server→machine fan-out frames. The upload path is upsert-style —
re-uploading the same filename in the same room overwrites the
existing file's bytes and metadata rather than appending a versioned
copy. See the implementation plan §3, decision 4.

The server is the source of truth for these files: the machine only
materializes them into ``<agent_root>/memory/shared/`` and never
sync-backs their contents (plan §3, decision 2). So every mutation
here emits a frame to every agent currently placed in the room, and
``backfill_agent`` / ``resync_machine`` handle reconnects by
re-emitting the same frames — they're idempotent thanks to the
``content_sha256`` check on the machine side.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Agent, Participant, RoomSharedFile
from doorae.rooms import file_storage
from doorae.scheduler.machine_bus import MachineBus


# Size ceiling for a single upload. Stays well under the default
# websockets frame limit (1 MiB) so the fan-out frame envelope fits
# alongside sha256 / agent_id / storage_name without bumping server
# or daemon config.
DEFAULT_MAX_SIZE_BYTES = 256 * 1024

# Text-only MIME whitelist. We inject these files directly into the
# agent's system prompt; accepting binary content would just waste
# tokens and risk encoding explosions mid-prompt.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "text/csv",
        "text/yaml",
        "text/x-yaml",
        "application/json",
        "application/yaml",
        "application/x-yaml",
        "text/x-python",
        "application/xml",
        "text/xml",
        "text/html",
    }
)


class UnsupportedMimeError(Exception):
    """Upload rejected for MIME type outside the text-only whitelist."""


class InvalidFilenameError(Exception):
    """Upload rejected because the filename could not be sanitised to a
    valid ``storage_name`` (e.g. empty, pure separators, reserved
    names)."""


_STORAGE_NAME_MAX_LEN = 200
_DISALLOWED_STORAGE_NAMES = frozenset({"", ".", ".."})


def sanitize_storage_name(filename: str) -> str:
    """Strip path components / control chars out of an uploaded
    filename so it's safe to drop into ``memory/shared/`` on the
    agent side.

    Returns the sanitised name. Raises :class:`InvalidFilenameError`
    if nothing usable remains. The sanitiser is intentionally strict
    — the agent never needs to see arbitrary user strings in its
    filesystem.
    """
    # Use only the basename — ``os.path.basename`` handles ``/`` and
    # ``\\`` even on Linux hosts so paste from Windows still
    # sanitises predictably.
    head = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    # Drop control characters entirely; keep printable ASCII + common
    # unicode. (We don't normalize NFC/NFD here — agents see whatever
    # the user typed.)
    head = re.sub(r"[\x00-\x1f\x7f]", "", head)
    # Truncate length but try not to split inside a UTF-8 char.
    head = head.encode("utf-8")[:_STORAGE_NAME_MAX_LEN].decode("utf-8", errors="ignore")
    if head in _DISALLOWED_STORAGE_NAMES:
        raise InvalidFilenameError(f"invalid filename: {filename!r}")
    return head


async def list_shared_files(
    session: AsyncSession, *, room_id: str
) -> list[RoomSharedFile]:
    """Return all shared files for a room, newest first."""
    stmt = (
        select(RoomSharedFile)
        .where(RoomSharedFile.room_id == room_id)
        .order_by(RoomSharedFile.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def upload_file(
    session: AsyncSession,
    *,
    room_files_dir: Path,
    room_id: str,
    uploader_user_id: str | None,
    filename: str,
    mime: str,
    stream: BinaryIO,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
) -> RoomSharedFile:
    """Persist an upload and return the committed ``RoomSharedFile``.

    If a file with the same sanitised ``storage_name`` already exists
    in the room, its bytes are replaced in place (the on-disk file
    uses ``os.rename`` so the swap is atomic) and its metadata row
    is updated — no new row is inserted. Callers that want to react
    to both create and replace cases should check ``file.created_at``
    against the call time.

    Raises:
        UnsupportedMimeError: ``mime`` is not in the whitelist.
        InvalidFilenameError: ``filename`` sanitises to nothing.
        FileTooLargeError: stream exceeded ``max_size_bytes``.
    """
    if mime not in ALLOWED_MIME_TYPES:
        raise UnsupportedMimeError(f"mime not allowed: {mime!r}")

    storage_name = sanitize_storage_name(filename)

    existing = await _find_by_storage_name(
        session, room_id=room_id, storage_name=storage_name
    )

    # Reuse the on-disk id for upserts so the path stays stable
    # (avoids orphaning the old file while a new id races in).
    file_id = existing.id if existing else str(uuid.uuid4())

    stored = file_storage.save_upload(
        room_files_dir=room_files_dir,
        room_id=room_id,
        file_id=file_id,
        stream=stream,
        max_size_bytes=max_size_bytes,
    )

    try:
        if existing is not None:
            existing.filename = filename
            existing.storage_path = stored.storage_path
            existing.sha256 = stored.sha256
            existing.size_bytes = stored.size_bytes
            existing.mime = mime
            existing.uploaded_by = uploader_user_id
            # created_at stays as-is: it tracks when the slot was
            # first claimed, not the latest revision. Callers that
            # want "last modified" semantics can derive from sha256
            # or add a column later.
            row = existing
        else:
            row = RoomSharedFile(
                id=file_id,
                room_id=room_id,
                filename=filename,
                storage_name=storage_name,
                storage_path=stored.storage_path,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                mime=mime,
                uploaded_by=uploader_user_id,
            )
            session.add(row)
        await session.commit()
    except BaseException:
        # Insert/update failed after we already wrote bytes — unlink
        # the orphan so it doesn't linger. For the upsert branch the
        # bytes have already replaced the previous version; there's
        # nothing sensible to "roll back" to. Startup ``cleanup_orphans``
        # reconciles anyway.
        if existing is None:
            file_storage.delete_file(room_files_dir, stored.storage_path)
        raise

    await session.refresh(row)
    return row


async def delete_shared_file(
    session: AsyncSession,
    *,
    room_files_dir: Path,
    file_id: str,
) -> RoomSharedFile | None:
    """Remove the DB row + on-disk bytes. Returns the deleted row so
    the caller can trigger a delete fan-out, or ``None`` when the
    file was already gone."""
    row = await session.get(RoomSharedFile, file_id)
    if row is None:
        return None

    storage_path = row.storage_path
    await session.delete(row)
    await session.commit()
    file_storage.delete_file(room_files_dir, storage_path)
    return row


async def fan_out_write(
    session: AsyncSession,
    *,
    machine_bus: MachineBus,
    room_files_dir: Path,
    file: RoomSharedFile,
) -> int:
    """Push ``file`` to every agent currently placed on a machine in
    the same room. Returns the number of frames that were successfully
    handed to a connected machine bus (for tests / observability —
    not a delivery receipt)."""
    content = file_storage.read_file(room_files_dir, file.storage_path)
    placements = await _placed_agents_in_room(session, room_id=file.room_id)

    sent = 0
    for agent_id, machine_id in placements:
        frame = {
            "type": "agent_memory_shared_file_write",
            "agent_id": agent_id,
            "storage_name": file.storage_name,
            "content": content,
            "content_sha256": file.sha256,
        }
        if await machine_bus.send(machine_id, frame):
            sent += 1
    return sent


async def fan_out_delete(
    session: AsyncSession,
    *,
    machine_bus: MachineBus,
    room_id: str,
    storage_name: str,
) -> int:
    """Tell every placed agent in the room to drop ``storage_name``."""
    placements = await _placed_agents_in_room(session, room_id=room_id)
    sent = 0
    for agent_id, machine_id in placements:
        frame = {
            "type": "agent_memory_shared_file_delete",
            "agent_id": agent_id,
            "storage_name": storage_name,
        }
        if await machine_bus.send(machine_id, frame):
            sent += 1
    return sent


async def backfill_agent(
    session: AsyncSession,
    *,
    machine_bus: MachineBus,
    room_files_dir: Path,
    room_id: str,
    agent_id: str,
) -> int:
    """Send every current shared file in the room to a single agent
    (e.g. just-joined, machine reconnect). Returns the number of
    frames successfully dispatched."""
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.placed_on_machine_id is None:
        return 0
    files = await list_shared_files(session, room_id=room_id)
    sent = 0
    for file in files:
        content = file_storage.read_file(room_files_dir, file.storage_path)
        frame = {
            "type": "agent_memory_shared_file_write",
            "agent_id": agent_id,
            "storage_name": file.storage_name,
            "content": content,
            "content_sha256": file.sha256,
        }
        if await machine_bus.send(agent.placed_on_machine_id, frame):
            sent += 1
    return sent


# ── internal helpers ────────────────────────────────────────────────


async def _find_by_storage_name(
    session: AsyncSession, *, room_id: str, storage_name: str
) -> RoomSharedFile | None:
    stmt = select(RoomSharedFile).where(
        RoomSharedFile.room_id == room_id,
        RoomSharedFile.storage_name == storage_name,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _placed_agents_in_room(
    session: AsyncSession, *, room_id: str
) -> list[tuple[str, str]]:
    """Return ``(agent_id, machine_id)`` pairs for every agent
    participant in the room that currently has a placement."""
    stmt = (
        select(Agent.id, Agent.placed_on_machine_id)
        .join(Participant, Participant.agent_id == Agent.id)
        .where(Participant.room_id == room_id)
        .where(Agent.placed_on_machine_id.isnot(None))
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]
