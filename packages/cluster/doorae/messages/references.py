"""Metadata reference canonicalization for room messages."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import RoomSharedFile


class InvalidSharedFileReference(ValueError):
    """Raised when metadata references a missing or inaccessible file."""


def _valid_origin(value: Any) -> str | None:
    return value if value in {"inline", "attachment"} else None


async def canonicalize_shared_file_references(
    db: AsyncSession,
    *,
    room_id: str,
    metadata: dict[str, Any],
    allow_shared_files: bool,
) -> dict[str, Any]:
    """Validate and canonicalize ``metadata.references`` shared files.

    Clients may send only a stable ``id``. Display names, storage names,
    and hashes are reloaded from ``room_shared_files`` so a forged
    payload cannot point at another room's file or spoof the agent-side
    ``memory/shared/<storage_name>`` path.
    """
    out = dict(metadata)
    raw = out.get("references")
    if raw is None:
        return out
    if not isinstance(raw, list):
        out.pop("references", None)
        return out

    ids: list[str] = []
    for ref in raw:
        if not isinstance(ref, dict) or ref.get("type") != "shared_file":
            continue
        if not allow_shared_files:
            raise InvalidSharedFileReference("shared file references are not allowed")
        file_id = ref.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise InvalidSharedFileReference("shared file reference missing id")
        ids.append(file_id)

    if not ids:
        out["references"] = [ref for ref in raw if isinstance(ref, dict)]
        return out

    rows = (
        (
            await db.execute(
                select(RoomSharedFile).where(
                    RoomSharedFile.room_id == room_id,
                    RoomSharedFile.id.in_(ids),
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in rows}
    missing = [file_id for file_id in ids if file_id not in by_id]
    if missing:
        raise InvalidSharedFileReference("shared file reference not found")

    seen_shared: set[str] = set()
    canonical: list[dict[str, Any]] = []
    for ref in raw:
        if not isinstance(ref, dict):
            continue
        if ref.get("type") != "shared_file":
            canonical.append(ref)
            continue
        file_id = ref["id"]
        if file_id in seen_shared:
            continue
        seen_shared.add(file_id)
        row = by_id[file_id]
        item: dict[str, Any] = {
            "type": "shared_file",
            "id": row.id,
            "name": row.filename,
            "storage_name": row.storage_name,
            "sha256": row.sha256,
        }
        origin = _valid_origin(ref.get("origin"))
        if origin is not None:
            item["origin"] = origin
        canonical.append(item)

    if canonical:
        out["references"] = canonical
    else:
        out.pop("references", None)
    return out
