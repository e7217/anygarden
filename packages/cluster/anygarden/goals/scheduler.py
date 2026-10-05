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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from anygarden.db.models import Goal, Task
from anygarden.goals.executor import GoalExecutionError, trigger_goal
from anygarden.goals.policy import (
    InvalidTriggerConfig,
    compute_next_run_at,
    validate_trigger_config,
)
from anygarden.goals.sweeper import sweep_stuck_tasks
from anygarden.tasks_status import TERMINAL_STATUSES

if TYPE_CHECKING:
    from anygarden.ws.manager import ConnectionManager

log = logging.getLogger(__name__)

# Default cadence — 30s is a comfortable balance between near-real-
# time triggers (the policy floor is 60s anyway) and idle CPU usage
# on a server with no goals registered.
DEFAULT_POLL_INTERVAL_SECONDS: float = 30.0

# #449 (Wave 1b) — stampede cap. A clock skew or a backlog (server
# was down) can leave hundreds of goals due at once; firing them all
# in one tick would hammer the engines. We claim at most this many
# oldest-due goals per tick (ASC order), and the rest roll into the
# next ~30s tick. Sizing: 25/tick × 2 ticks/min = up to 50 fires/min
# steady-state, comfortably above a realistic active-goal count while
# bounding the burst.
MAX_GOALS_PER_TICK: int = 25


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _has_open_run():
    """Include input/dependency waits in the schedule's overlap policy."""
    return exists().where(
        Task.goal_id == Goal.id,
        Task.status.not_in(TERMINAL_STATUSES),
    )


class ScheduledEventError(GoalExecutionError):
    """A supplied slot is not an authoritative scheduled delivery."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ScheduledEventClaim:
    task: Task | None
    duplicate: bool = False
    waiting_overlap: bool = False


async def _scheduled_slot_task(db, goal_id: str, slot: datetime) -> Task | None:
    """Validate immutable slot provenance before treating a key as a replay."""
    task = await db.scalar(select(Task).where(
        Task.goal_id == goal_id,
        Task.idempotency_key == f"{goal_id}:{int(slot.timestamp())}",
    ))
    if task is None:
        return None
    context = task.schedule_context or {}
    try:
        recorded = datetime.fromisoformat(context["scheduled_for"])
        ZoneInfo(context["timezone"])
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        raise ScheduledEventError("SCHEDULED_SLOT_CONFLICT") from None
    if (recorded.tzinfo is None or recorded.utcoffset() is None
        or recorded.astimezone(UTC) != slot.astimezone(UTC)
        or task.triggered_by != "scheduler"
        or context.get("goal_id") != goal_id
        or context.get("trigger_source") != "scheduler"
        or context.get("overlap_policy") != "wait"):
        raise ScheduledEventError("SCHEDULED_SLOT_CONFLICT")
    return task


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
        manager: ConnectionManager | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval = poll_interval_seconds
        # #314 — held so each ``trigger_goal`` call can fanout the
        # synthetic mention frame on the room channel. ``None`` is the
        # backwards-compatible default for tests / callers that don't
        # wire one up.
        self._manager = manager
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """Spawn the polling loop. Idempotent — multiple ``start``
        calls without a ``stop`` between them are no-ops."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="anygarden-goal-scheduler")
        log.info("goal_scheduler_started", extra={"interval": self._poll_interval})

    async def stop(self) -> None:
        """Signal the loop to exit and await its cleanup."""
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
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
            # #314 — stuck-task sweep runs in its own session so a
            # sweep error can't corrupt the goal-trigger transaction
            # above and vice versa. Same poll cadence as the trigger
            # path: ~30s is enough granularity for 2-minute pickup /
            # 10-minute execution timeouts.
            try:
                await self._sweep()
            except Exception:  # pragma: no cover — defensive
                log.exception("goal_scheduler_sweep_failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
            except TimeoutError:
                continue
            else:
                break

    async def _tick(self) -> None:
        """Find every active goal whose ``next_run_at`` is in the past
        and fire it *exactly once* (#449).

        For each due goal we:
        1. compute the next slot (``compute_next_run_at``) up front,
        2. skip it if a nonterminal Task (including ``blocked``)
           already exists for it — in-flight dedup so a slow run does
           not get a sibling fire,
        3. issue an atomic guarded ``UPDATE ... WHERE next_run_at <=
           now`` (the CAS claim). Only the writer whose UPDATE matches
           a row (``rowcount == 1``) advances the schedule and fires;
           concurrent ticks / replicas that lose the race see
           ``rowcount == 0`` and move on. We use ``rowcount`` rather
           than ``UPDATE ... RETURNING`` because it is portable across
           SQLite and Postgres (RETURNING on UPDATE needs sqlite
           >= 3.35; rowcount on a guarded single-row UPDATE is exact
           on both).

        Each goal gets its own commit so a single bad goal can't
        poison the others. The per-tick cap (``MAX_GOALS_PER_TICK``)
        bounds a backlog burst — oldest-due first, the rest roll into
        the next tick.
        """
        async with self._session_factory() as db:
            now = _utcnow()
            stmt = (
                select(Goal.id)
                .where(
                    Goal.status == "active",
                    Goal.next_run_at <= now,
                    ~_has_open_run(),
                )
                .order_by(Goal.next_run_at.asc())
                .limit(MAX_GOALS_PER_TICK)
            )
            due_ids = (await db.execute(stmt)).scalars().all()
            if not due_ids:
                return
            log.debug("goal_scheduler_due", extra={"count": len(due_ids)})
            for goal_id in due_ids:
                try:
                    await self._claim_and_fire(db, goal_id, now)
                except (GoalExecutionError, InvalidTriggerConfig) as exc:
                    # Pause the goal so the loop doesn't retry the
                    # same broken state every tick. The owner will
                    # see the paused state in the UI and re-add the
                    # agent / point at a different room.
                    log.warning(
                        "goal_pause_due_to_execution_error",
                        extra={"goal_id": goal_id, "error": str(exc)},
                    )
                    await db.rollback()
                    goal = await db.get(Goal, goal_id)
                    if goal is not None and goal.status == "active":
                        goal.status = "paused"
                        await db.commit()
                except Exception:  # pragma: no cover — defensive
                    log.exception(
                        "goal_trigger_unexpected_failure",
                        extra={"goal_id": goal_id},
                    )
                    await db.rollback()

    async def _claim_and_fire(
        self, db: AsyncSession, goal_id: str, now: datetime,
        *, scheduled_for: datetime | None = None,
    ) -> ScheduledEventClaim | None:
        """Claim one due goal via CAS and fire it. Commits on success.

        No-ops (without raising) if the goal vanished, already has an
        in-flight Task, or the CAS lost the race — the caller's outer
        ``try`` only needs to handle the firing failure modes.
        """
        supplied_slot = scheduled_for is not None
        if supplied_slot:
            if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
                raise ScheduledEventError("SCHEDULED_TIMEZONE_REQUIRED")
            scheduled_for = scheduled_for.astimezone(UTC)
            if scheduled_for > now:
                raise ScheduledEventError("SCHEDULED_SLOT_FUTURE")
        goal = await db.get(Goal, goal_id, populate_existing=supplied_slot)
        if supplied_slot:
            if goal is None:
                raise ScheduledEventError("GOAL_NOT_FOUND")
            if goal.trigger_type not in {"cron", "interval"}:
                raise ScheduledEventError("SCHEDULED_GOAL_REQUIRED")
            replay = await _scheduled_slot_task(db, goal_id, scheduled_for)
            if replay is not None:
                return ScheduledEventClaim(replay, duplicate=True)
            if goal.status != "active":
                raise ScheduledEventError("GOAL_NOT_ACTIVE")
            if goal.next_run_at != scheduled_for:
                raise ScheduledEventError("SCHEDULED_SLOT_NOT_CURRENT")
        if goal is None or goal.status != "active" or goal.next_run_at is None:
            return

        # In-flight dedup — a goal whose previous fire is still waiting
        # or running must not get a sibling. Reuses the
        # ``ix_tasks_goal_created`` index (goal_id leading column).
        in_flight = (
            await db.execute(
                select(Task.id)
                .where(
                    Task.goal_id == goal_id,
                    Task.status.not_in(TERMINAL_STATUSES),
                )
                .limit(1)
            )
        ).first()
        if in_flight is not None:
            log.debug("goal_skip_in_flight", extra={"goal_id": goal_id})
            if supplied_slot:
                return ScheduledEventClaim(None, waiting_overlap=True)
            return

        # The slot we are about to consume — captured BEFORE the CAS
        # advance so it keys the idempotency token.
        slot = goal.next_run_at
        validate_trigger_config(goal.trigger_type, goal.trigger_config)
        next_slot = compute_next_run_at(
            goal.trigger_type, goal.trigger_config, after=now
        )

        # Atomic CAS claim. Guard on the same predicate the SELECT
        # used; only the winner advances + fires.
        result = await db.execute(
            update(Goal)
            .where(
                Goal.id == goal_id,
                Goal.status == "active",
                Goal.next_run_at == slot,
                Goal.next_run_at <= now,
                Goal.updated_at == goal.updated_at,
                ~_has_open_run(),
            )
            .values(next_run_at=next_slot, last_run_at=now, claimed_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            # Lost the race (another tick / replica claimed it) or the
            # row changed under us. Drop the stale in-session UPDATE.
            log.debug("goal_claim_lost", extra={"goal_id": goal_id})
            await db.rollback()
            if supplied_slot:
                replay = await _scheduled_slot_task(db, goal_id, scheduled_for)
                if replay is not None:
                    return ScheduledEventClaim(replay, duplicate=True)
                current = await db.get(Goal, goal_id, populate_existing=True)
                if current is None:
                    raise ScheduledEventError("GOAL_NOT_FOUND")
                if current.status != "active":
                    raise ScheduledEventError("GOAL_NOT_ACTIVE")
                if current.next_run_at == scheduled_for and await db.scalar(
                    select(Task.id).where(Task.goal_id == goal_id,
                        Task.status.not_in(TERMINAL_STATUSES)).limit(1)
                ):
                    return ScheduledEventClaim(None, waiting_overlap=True)
                raise ScheduledEventError("SCHEDULED_SLOT_CHANGED")
            return

        # Keep the in-session ORM object consistent with what the Core
        # UPDATE wrote, so the ORM flush at commit doesn't clobber the
        # CAS'd schedule with stale attribute values.
        goal.next_run_at = next_slot
        goal.claimed_at = now

        idempotency_key = f"{goal_id}:{int(slot.timestamp())}"
        # A completed Run-now can already own the slot key. Consume the due
        # slot without a second Task/assignment; the strict delivery path
        # rejects that manual provenance before its claim instead.
        try:
            task = await db.scalar(select(Task).where(
                Task.goal_id == goal_id, Task.idempotency_key == idempotency_key,
            ))
            duplicate = task is not None
            if task is None:
                task = await trigger_goal(
                    db,
                    goal,
                    trigger_source="scheduler",
                    idempotency_key=idempotency_key,
                    scheduled_for=slot,
                    manager=self._manager,
                )
            elif supplied_slot:
                task = await _scheduled_slot_task(db, goal_id, slot)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            if not supplied_slot:
                raise
            task = await _scheduled_slot_task(db, goal_id, slot)
            if task is None:
                raise ScheduledEventError("SCHEDULED_SLOT_CONFLICT") from None
            return ScheduledEventClaim(task, duplicate=True)
        return ScheduledEventClaim(task, duplicate=duplicate)

    async def _sweep(self) -> None:
        """Run one stuck-task sweep in its own short session (#314).

        Isolated from ``_tick`` so a sweep error doesn't roll back
        the goal-trigger commits and vice versa. The sweeper itself
        scopes per-task work; we just commit once at the end.
        """
        async with self._session_factory() as db:
            await sweep_stuck_tasks(db, manager=self._manager, now=_utcnow())
            await db.commit()
