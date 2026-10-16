"""Metadata reference canonicalization for room messages."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import RoomArtifact, RoomSharedFile


class InvalidSharedFileReference(ValueError):
    """Raised when metadata references a missing or inaccessible file."""


def _valid_origin(value: Any) -> str | None:
    return value if value in {"inline", "attachment"} else None


def _reference_ids(raw: list[Any], ref_type: str) -> list[str]:
    ids: list[str] = []
    for ref in raw:
        if not isinstance(ref, dict) or ref.get("type") != ref_type:
            continue
        file_id = ref.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise InvalidSharedFileReference(f"{ref_type} reference missing id")
        ids.append(file_id)
    return ids


async def canonicalize_message_references(
    db: AsyncSession,
    *,
    room_id: str,
    metadata: dict[str, Any],
    allow_shared_files: bool,
    sender_agent_id: str | None = None,
    allow_room_artifacts: bool = False,
) -> dict[str, Any]:
    """Validate and canonicalize ``metadata.references``.

    Clients may send only a stable ``id``. Display fields are reloaded
    from the database so a forged payload cannot point at another room's
    file or spoof a name, MIME type, or agent-side path:

    - ``shared_file`` — a user upload, rebuilt from ``room_shared_files``
      (including the ``memory/shared/<room_id>/<storage_name>`` path).
    - ``artifact`` — a file an agent attached to its own reply (#854),
      rebuilt from ``room_artifacts``. An agent may reference only the
      artifacts it produced. A person may reference any artifact of the
      room when ``allow_room_artifacts`` is set (#873): people attach
      images through artifacts, every participant can already see them,
      and uploads dedupe on bytes, so "their own" would reject a picture
      an agent happened to store first. Otherwise artifact references
      are dropped.

    Reference types outside these two are dropped.
    """
    out = dict(metadata)
    raw = out.get("references")
    if raw is None:
        return out
    if not isinstance(raw, list):
        out.pop("references", None)
        return out

    shared_ids = _reference_ids(raw, "shared_file")
    if shared_ids and not allow_shared_files:
        raise InvalidSharedFileReference("shared file references are not allowed")
    artifact_ids = (
        _reference_ids(raw, "artifact")
        if sender_agent_id or allow_room_artifacts
        else []
    )

    shared_by_id: dict[str, RoomSharedFile] = {}
    if shared_ids:
        rows = (
            await db.execute(
                select(RoomSharedFile).where(
                    RoomSharedFile.room_id == room_id,
                    RoomSharedFile.id.in_(shared_ids),
                )
            )
        ).scalars().all()
        shared_by_id = {row.id: row for row in rows}
        if any(file_id not in shared_by_id for file_id in shared_ids):
            raise InvalidSharedFileReference("shared file reference not found")

    artifact_by_id: dict[str, RoomArtifact] = {}
    if artifact_ids:
        query = select(RoomArtifact).where(
            RoomArtifact.room_id == room_id,
            RoomArtifact.id.in_(artifact_ids),
        )
        if sender_agent_id:
            query = query.where(RoomArtifact.produced_by_agent_id == sender_agent_id)
        rows = (await db.execute(query)).scalars().all()
        artifact_by_id = {row.id: row for row in rows}
        if any(file_id not in artifact_by_id for file_id in artifact_ids):
            raise InvalidSharedFileReference("artifact reference not found")

    seen: set[tuple[str, str]] = set()
    canonical: list[dict[str, Any]] = []
    for ref in raw:
        if not isinstance(ref, dict):
            continue
        ref_type = ref.get("type")
        file_id = ref.get("id")
        if ref_type == "shared_file":
            row = shared_by_id[file_id]
            item: dict[str, Any] = {
                "type": "shared_file",
                "id": row.id,
                "room_id": row.room_id,
                "name": row.filename,
                "storage_name": row.storage_name,
                "sha256": row.sha256,
            }
            origin = _valid_origin(ref.get("origin"))
            if origin is not None:
                item["origin"] = origin
        elif ref_type == "artifact" and file_id in artifact_by_id:
            artifact = artifact_by_id[file_id]
            item = {
                "type": "artifact",
                "id": artifact.id,
                "room_id": artifact.room_id,
                "name": artifact.filename,
                "mime": artifact.mime,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            }
        else:
            continue
        key = (ref_type, file_id)
        if key in seen:
            continue
        seen.add(key)
        canonical.append(item)

    if canonical:
        out["references"] = canonical
    else:
        out.pop("references", None)
    return out
