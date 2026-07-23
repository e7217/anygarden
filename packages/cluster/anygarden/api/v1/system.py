"""REST endpoints for system version + PyPI update detection — ``/api/v1/system`` (#546).

- ``GET /version``  — the running server version (any logged-in user).
- ``GET /updates``  — cached update status (admin); never calls out.
- ``POST /check-updates`` — refresh the cache from PyPI (admin).

The read/write split keeps "manual now, automatic later" cheap: the UI
only ever reads ``/updates`` (the cache), so a future background poller
can fill the same cache via the same service with no endpoint change.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.dependencies import forbid_guest, get_admin_identity, get_db
from anygarden.system import version_service, version_store

router = APIRouter(prefix="/api/v1/system", tags=["system"])

# Packages the update check tracks. ``anygarden`` is the server itself;
# ``anygarden-machine`` lets the admin see the recommended machine version.
_CHECK_PACKAGES = ["anygarden", "anygarden-machine"]


class ServerVersion(BaseModel):
    version: str


class PackageUpdate(BaseModel):
    package: str
    current: str
    latest: str | None
    update_available: bool
    checked_at: datetime | None
    error: str | None


def _to_update(
    package: str,
    *,
    latest: str | None,
    checked_at: datetime | None,
    error: str | None,
) -> PackageUpdate:
    current = version_service.get_local_version(package)
    return PackageUpdate(
        package=package,
        current=current,
        latest=latest,
        update_available=version_service.is_update_available(current, latest),
        checked_at=checked_at,
        error=error,
    )


async def _read_updates(db: AsyncSession) -> list[PackageUpdate]:
    rows = {r.package: r for r in await version_store.get_all(db)}
    result: list[PackageUpdate] = []
    for package in _CHECK_PACKAGES:
        row = rows.get(package)
        result.append(
            _to_update(
                package,
                latest=row.latest_version if row else None,
                checked_at=row.checked_at if row else None,
                error=row.error if row else None,
            )
        )
    return result


@router.get("/version", response_model=ServerVersion)
async def get_version(_: Identity = Depends(forbid_guest)) -> ServerVersion:
    """Return the running server version (all logged-in users)."""
    return ServerVersion(version=version_service.get_local_version("anygarden"))


@router.get("/updates", response_model=list[PackageUpdate])
async def get_updates(
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> list[PackageUpdate]:
    """Return the cached update status per package (no outbound call)."""
    return await _read_updates(db)


@router.post("/check-updates", response_model=list[PackageUpdate])
async def check_updates(
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> list[PackageUpdate]:
    """Query PyPI for each tracked package, refresh the cache, and return it."""
    now = datetime.now(timezone.utc)
    for package in _CHECK_PACKAGES:
        latest = await version_service.fetch_pypi_latest(package)
        await version_store.upsert(
            db,
            package=package,
            latest_version=latest,
            checked_at=now,
            error=None if latest is not None else "unreachable",
        )
    await db.commit()
    return await _read_updates(db)
