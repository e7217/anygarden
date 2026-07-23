"""DB cache for the last PyPI update check per package (#546).

The admin ``check-updates`` endpoint (and, later, a background poller)
write here; the ``updates`` endpoint reads here without any outbound
call. One row per package.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import VersionCheck


async def upsert(
    session: AsyncSession,
    *,
    package: str,
    latest_version: str | None,
    checked_at: datetime,
    error: str | None = None,
) -> VersionCheck:
    """Insert or update the cache row for ``package``.

    A ``latest_version`` of ``None`` (a failed check) preserves the
    previously-stored value so the UI can still show the last-known
    latest alongside the recorded ``error`` — only a successful check
    with a concrete version overwrites it.
    """
    row = await session.get(VersionCheck, package)
    if row is None:
        row = VersionCheck(package=package)
        session.add(row)
    if latest_version is not None:
        row.latest_version = latest_version
    row.checked_at = checked_at
    row.error = error
    await session.flush()
    return row


async def get_all(session: AsyncSession) -> list[VersionCheck]:
    """Return all cached version-check rows."""
    result = await session.execute(select(VersionCheck))
    return list(result.scalars().all())
