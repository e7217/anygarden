"""Background PyPI update check (SYS-06).

Fills the same ``version_checks`` cache the admin ``POST /check-updates``
writes, through the same ``check_and_store`` routine, so the UI (which only
reads ``GET /updates``) learns about a new release without a manual click.

Controlled by ``AnygardenSettings.update_check_interval_hours``
(``ANYGARDEN_UPDATE_CHECK_INTERVAL_HOURS``, default 24). ``0`` or a negative
value disables the loop entirely — no outbound call is ever made — for
air-gapped installs. The first check runs shortly after startup.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.system import version_service, version_store

log = structlog.get_logger("system.update_check")

# Packages the update check tracks. #754 — the machine daemon ships inside
# ``anygarden``, so one package covers both the server and the daemons.
CHECK_PACKAGES: list[str] = ["anygarden"]

# Delay before the first background check so it doesn't compete with the
# rest of startup (capped by the interval itself).
_WARMUP_SECONDS = 60.0


async def check_and_store(db: AsyncSession) -> None:
    """Query PyPI for each tracked package and upsert the cache (no commit).

    ``fetch_pypi_latest`` never raises on network failure; a ``None`` result
    is recorded as ``error="unreachable"`` while keeping the last known
    latest version.
    """
    now = datetime.now(UTC)
    for package in CHECK_PACKAGES:
        latest = await version_service.fetch_pypi_latest(package)
        await version_store.upsert(
            db,
            package=package,
            latest_version=latest,
            checked_at=now,
            error=None if latest is not None else "unreachable",
        )


async def run_update_check_loop(
    session_factory: Callable[[], AsyncSession],
    *,
    interval_seconds: float,
    warmup_seconds: float = _WARMUP_SECONDS,
) -> None:
    """Refresh the update cache every ``interval_seconds`` until cancelled.

    Any per-check exception is logged and swallowed so a transient failure
    never stops the loop or crashes the app.
    """
    try:
        await asyncio.sleep(min(warmup_seconds, interval_seconds))
    except asyncio.CancelledError:
        return

    while True:
        try:
            async with session_factory() as db:
                await check_and_store(db)
                await db.commit()
            log.info("update_check.done", packages=CHECK_PACKAGES)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("update_check.error", error=str(exc))

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


def start_update_check(app: FastAPI) -> None:
    """Start the background loop on ``app.state.update_check_task``.

    No-op when disabled (interval <= 0) or when a task (or test double) is
    already present. Stopped by the server shutdown path.
    """
    if getattr(app.state, "update_check_task", None) is not None:
        return
    hours = float(getattr(app.state.config, "update_check_interval_hours", 0) or 0)
    if hours <= 0:
        return
    app.state.update_check_task = asyncio.create_task(
        run_update_check_loop(
            lambda: app.state.session_factory(),
            interval_seconds=hours * 3600.0,
        ),
        name="update_check",
    )
