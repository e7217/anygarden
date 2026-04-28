"""Goal Scheduler (#302 Phase 2).

In-process polling loop. Wakes every ``poll_interval`` seconds and
fires every ``Goal`` whose ``next_run_at`` has elapsed. Keeps a
single global instance attached to the FastAPI lifespan; the cluster
is single-instance for now (multi-replica advisory locking lands in
#302 Phase 3).

Picked over APScheduler because:
- The MVP has one cluster process — no jobstore-backed coordination
  needed.
- ``croniter`` already in deps for the policy module covers cron
  parsing; we just compute next-fire ourselves and store it on the
  Goal row.
- A polling loop is ~80 lines and explicit; APScheduler integration
  with SQLAlchemy v2 + FastAPI lifespan is a much larger surface to
  test.

The scheduler is intentionally forgiving:
- Per-goal exceptions are logged and the goal moves on to the next
  cycle (or is paused after consecutive failures by the executor).
- A clock skew where ``next_run_at`` lags by hours fires once and
  rolls forward to the future — we don't replay missed runs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from doorae.db.models import Goal
from doorae.goals.executor import GoalExecutionError, trigger_goal

log = logging.getLogger(__name__)

# Default cadence — 30s is a comfortable balance between near-real-
# time triggers (the policy floor is 60s anyway) and idle CPU usage
# on a server with no goals registered.
DEFAULT_POLL_INTERVAL_SECONDS: float = 30.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GoalScheduler:
    """Async polling loop. Lifespan-managed by FastAPI.

    Usage:
        scheduler = GoalScheduler(session_factory)
        scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """Spawn the polling loop. Idempotent — multiple ``start``
        calls without a ``stop`` between them are no-ops."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run(), name="doorae-goal-scheduler"
        )
        log.info("goal_scheduler_started", extra={"interval": self._poll_interval})

    async def stop(self) -> None:
        """Signal the loop to exit and await its cleanup."""
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            log.warning("goal_scheduler_stop_timeout")
            self._task.cancel()
        finally:
            self._task = None
            log.info("goal_scheduler_stopped")

    async def _run(self) -> None:
        """Polling loop. Wakes every ``poll_interval`` or when
        ``_stop_event`` is set, whichever comes first."""
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception:  # pragma: no cover — defensive
                # Never let a single tick crash the loop. Errors are
                # already logged by ``_tick`` for the per-goal path;
                # this catches anything from session setup.
                log.exception("goal_scheduler_tick_failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                continue
            else:
                break

    async def _tick(self) -> None:
        """Find every active goal whose ``next_run_at`` is in the
        past and fire it once. Each goal gets its own short-lived
        session so a single bad goal can't poison the others."""
        async with self._session_factory() as db:
            now = _utcnow()
            stmt = (
                select(Goal)
                .where(Goal.status == "active", Goal.next_run_at <= now)
                .order_by(Goal.next_run_at.asc())
            )
            due = (await db.execute(stmt)).scalars().all()
            if not due:
                return
            log.debug("goal_scheduler_due", extra={"count": len(due)})
            for goal in due:
                try:
                    await trigger_goal(db, goal)
                    await db.commit()
                except GoalExecutionError as exc:
                    # Pause the goal so the loop doesn't retry the
                    # same broken state every tick. The owner will
                    # see the paused state in the UI and re-add the
                    # agent / point at a different room.
                    log.warning(
                        "goal_pause_due_to_execution_error",
                        extra={"goal_id": goal.id, "error": str(exc)},
                    )
                    await db.rollback()
                    goal.status = "paused"
                    await db.commit()
                except Exception:  # pragma: no cover — defensive
                    log.exception(
                        "goal_trigger_unexpected_failure",
                        extra={"goal_id": goal.id},
                    )
                    await db.rollback()
