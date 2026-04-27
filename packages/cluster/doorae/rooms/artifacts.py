"""Room artifact service (#290 Phase B).

Handles ``room_artifact_produced`` frames coming from the machine
daemon: validate payload, fan-out to every room the producing agent
is currently placed in, persist bytes + DB row, broadcast
``room_artifact.added`` to live WebSocket subscribers.

Distinct from :mod:`doorae.rooms.shared_files` — that module covers
the user → agent input flow; this one covers the agent → user output
flow. The two share patterns (disk + DB split, sha256 dedup) but
their MIME / size policies and broadcast directions are inverted, so
they live as siblings rather than getting merged.

Routing decision (plan §3.2 D8): for the first cut we fan-out to
*every* room the producing agent is placed in, with sha256 dedup per
room. Tighter scoping (mention-driven, last-spoken-room) is a
follow-up once real usage tells us which heuristic feels right.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Agent, Participant, RoomArtifact
from doorae.rooms import artifact_storage
from doorae.rooms.shared_files import (
    InvalidFilenameError,
    sanitize_storage_name,
)

log = structlog.get_logger()


# Server-side limits. ``ARTIFACT_MAX_BYTES`` matches the daemon's cap
# so a malicious or buggy daemon can't smuggle oversize blobs past
# the wire-level check. ``ALLOWED_MIME_TYPES`` is broader than the
# daemon's whitelist for forward compatibility (a daemon ahead of
# the cluster shouldn't be able to push a MIME the cluster will
# nominally accept), but symmetrical for the headline use cases.
ARTIFACT_MAX_BYTES = 768 * 1024
ALLOWED_MIME_TYPES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
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
})


class ArtifactRejectedError(Exception):
    """Wrapped reason a frame was dropped before persistence.

    Caller logs the reason and continues — the WebSocket loop must
    not tear down on individual frame validation failures.
    """


def _validate_frame(frame: dict[str, Any]) -> tuple[str, str, str, bytes, str, int]:
    """Return ``(agent_id, filename, mime, raw, sha256, size)`` or raise."""
    agent_id = frame.get("agent_id") or ""
    filename = frame.get("filename") or ""
    mime = frame.get("mime") or ""
    content_b64 = frame.get("content_b64") or ""
    claimed_sha = frame.get("sha256") or ""
    claimed_size = frame.get("size_bytes")

    if not agent_id:
        raise ArtifactRejectedError("missing agent_id")
    if mime not in ALLOWED_MIME_TYPES:
        raise ArtifactRejectedError(f"mime {mime!r} not allowed")
    try:
        raw = base64.b64decode(content_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ArtifactRejectedError(f"invalid base64: {exc}") from exc
    size = len(raw)
    if claimed_size is not None and claimed_size != size:
        raise ArtifactRejectedError(
            f"size mismatch: claimed={claimed_size} actual={size}"
        )
    if size > ARTIFACT_MAX_BYTES:
        raise ArtifactRejectedError(
            f"size {size} exceeds cap {ARTIFACT_MAX_BYTES}"
        )
    actual_sha = hashlib.sha256(raw).hexdigest()
    if claimed_sha and claimed_sha != actual_sha:
        raise ArtifactRejectedError(
            f"sha256 mismatch: claimed={claimed_sha} actual={actual_sha}"
        )
    try:
        safe_name = sanitize_storage_name(filename)
    except InvalidFilenameError as exc:
        raise ArtifactRejectedError(str(exc)) from exc
    return agent_id, safe_name, mime, raw, actual_sha, size


async def handle_artifact_produced(
    session: AsyncSession,
    frame: dict[str, Any],
    *,
    artifact_files_dir: Path,
) -> list[RoomArtifact]:
    """Persist *frame* into every room the producing agent participates
    in. Returns the freshly inserted rows (omitting silently dedup'd
    duplicates) so the caller can broadcast ``room_artifact.added``.

    Idempotent thanks to ``UniqueConstraint(room_id, sha256)`` —
    re-delivery during reconnect lands as a server-side no-op.
    """
    try:
        agent_id, filename, mime, raw, sha256, size = _validate_frame(frame)
    except ArtifactRejectedError as exc:
        log.warning(
            "room_artifact_rejected",
            reason=str(exc),
            agent_id=frame.get("agent_id"),
            filename=frame.get("filename"),
        )
        return []

    # Confirm the agent actually exists. A frame referencing an
    # unknown agent_id is a server/machine drift bug — log and drop.
    agent = await session.get(Agent, agent_id)
    if agent is None:
        log.warning("room_artifact_unknown_agent", agent_id=agent_id)
        return []

    # Find every room this agent is currently placed in. Use
    # Participant rows because that's the source of truth for
    # placement; ``Agent.placed_on_machine_id`` only tells us which
    # *machine* hosts it, not which rooms.
    rows = (
        await session.execute(
            select(Participant.room_id).where(
                Participant.agent_id == agent_id
            )
        )
    ).scalars().all()
    target_rooms = list(dict.fromkeys(rows))  # preserve order, dedup

    if not target_rooms:
        log.info("room_artifact_no_target_rooms", agent_id=agent_id)
        return []

    inserted: list[RoomArtifact] = []
    for room_id in target_rooms:
        artifact = RoomArtifact(
            room_id=room_id,
            produced_by_agent_id=agent_id,
            filename=filename,
            storage_path="",  # filled after disk write
            sha256=sha256,
            size_bytes=size,
            mime=mime,
        )
        session.add(artifact)
        try:
            await session.flush()
        except IntegrityError:
            # ``(room_id, sha256)`` collision → duplicate re-delivery.
            # Roll back the failed flush and move on without raising.
            await session.rollback()
            log.info(
                "room_artifact_dedup",
                room_id=room_id,
                sha256=sha256,
                agent_id=agent_id,
            )
            continue

        # Disk write happens AFTER the row insert so that a write
        # failure rolls back the row in the same transaction. The
        # storage_path mirrors the row id.
        try:
            stored = artifact_storage.save_bytes(
                artifact_files_dir=artifact_files_dir,
                room_id=room_id,
                file_id=artifact.id,
                data=raw,
                max_size_bytes=ARTIFACT_MAX_BYTES,
            )
        except Exception as exc:
            await session.rollback()
            log.error(
                "room_artifact_disk_write_failed",
                room_id=room_id,
                agent_id=agent_id,
                error=str(exc),
            )
            continue
        artifact.storage_path = stored.storage_path
        try:
            await session.commit()
        except Exception as exc:
            # Commit failure leaves the disk file orphaned — clean up.
            artifact_storage.delete_file(
                artifact_files_dir, stored.storage_path
            )
            await session.rollback()
            log.error(
                "room_artifact_commit_failed",
                room_id=room_id,
                agent_id=agent_id,
                error=str(exc),
            )
            continue
        inserted.append(artifact)

    return inserted


async def list_artifacts(
    session: AsyncSession, *, room_id: str
) -> list[RoomArtifact]:
    """Return every artifact in *room_id*, newest first."""
    stmt = (
        select(RoomArtifact)
        .where(RoomArtifact.room_id == room_id)
        .order_by(RoomArtifact.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def get_artifact(
    session: AsyncSession, *, room_id: str, artifact_id: str
) -> RoomArtifact | None:
    """Look up a single artifact by id, scoped to its room."""
    row = await session.get(RoomArtifact, artifact_id)
    if row is None or row.room_id != room_id:
        return None
    return row


async def delete_artifact(
    session: AsyncSession,
    *,
    artifact_files_dir: Path,
    room_id: str,
    artifact_id: str,
) -> bool:
    """Drop an artifact's row + disk blob. Returns True on success,
    False when the artifact didn't exist (caller maps to 404).
    """
    row = await get_artifact(session, room_id=room_id, artifact_id=artifact_id)
    if row is None:
        return False
    storage_path = row.storage_path
    await session.delete(row)
    await session.commit()
    artifact_storage.delete_file(artifact_files_dir, storage_path)
    return True
