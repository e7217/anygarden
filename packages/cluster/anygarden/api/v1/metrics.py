"""Admin-only collaboration health metrics (turns, tasks, cost)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.api.v1.usage import _parse_window
from anygarden.auth.dependencies import Identity
from anygarden.dependencies import get_admin_identity, get_db
from anygarden.observability.collaboration import (
    collect_collaboration_metrics,
    project_room_ids,
)

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


@router.get("/collaboration")
async def collaboration_metrics(
    window: str = "24h",
    since: datetime | None = Query(
        default=None, description="ISO timestamp; overrides window"
    ),
    project_id: str | None = None,
    stuck_after_minutes: int = Query(default=15, ge=1, le=24 * 60),
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Turn delivery, task completion and cost for one window.

    The benchmark harness passes ``since`` (run start) and ``project_id``
    (the run's fresh project) to measure exactly one scenario run.
    """
    now = datetime.now(UTC)
    if since is None:
        since = now - timedelta(hours=_parse_window(window))
    elif since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    room_ids = None if project_id is None else await project_room_ids(db, project_id)
    return await collect_collaboration_metrics(
        db,
        since=since,
        room_ids=room_ids,
        now=now,
        stuck_after=timedelta(minutes=stuck_after_minutes),
    )
