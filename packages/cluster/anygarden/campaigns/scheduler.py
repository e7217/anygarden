"""Campaign scheduler (#930 §3.2).

In-process polling loop, lifespan-managed next to the Goal scheduler.
Each tick picks active campaigns whose next cycle is due (or whose
deadline has passed) and that have no open cycle, then advances each one
in its own short write transaction:

- room gone or archived → ``stopped``
- deadline passed → ``deadline_reached`` (no new cycle, CMP-09)
- cycle budget used up → ``budget_exhausted`` (CMP-10)
- the lead agent left the room → ``blocked``
- otherwise open cycle N: claim it with a CAS on ``state_revision``, then
  create the cycle Task (``campaign_id``, idempotency key
  ``campaign:{id}:{N}``) through the shared ``materialize_assigned_task``
  and a ``CampaignAttempt`` row — all in one commit.

The CAS plus the unique ``(campaign_id, cycle_no)`` and Task idempotency
key keep one open cycle per campaign across concurrent ticks and restarts
(CMP-02). Closing cycles belongs to the execution / evaluation hooks; the
tick only reacts to state it finds.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from anygarden.campaigns.brief import render_brief
from anygarden.campaigns.evaluation import (
    dispatch_eval,
    install_dispatcher,
    request_pending_evals,
    uninstall_dispatcher,
)
from anygarden.campaigns.lifecycle import expire_eval_deadlines, reconcile_open_cycles
from anygarden.campaigns.service import (
    Clock,
    apply_transition,
    cas_update,
    find_agent_participant,
    record_event,
    remaining_cycle_tokens,
    utcnow,
)
from anygarden.db.engine import begin_write_transaction
from anygarden.db.models import Campaign, CampaignAttempt, Room
from anygarden.task_materialize import materialize_assigned_task

if TYPE_CHECKING:
    from anygarden.scheduler.machine_bus import MachineBus
    from anygarden.ws.manager import ConnectionManager

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS: float = 30.0
MAX_CAMPAIGNS_PER_TICK: int = 25
SYSTEM_ACTOR = "system:scheduler"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _has_open_attempt():
    return exists().where(
        CampaignAttempt.campaign_id == Campaign.id,
        CampaignAttempt.closed_at.is_(None),
    )


def cycle_idempotency_key(campaign_id: str, cycle_no: int) -> str:
    return f"campaign:{campaign_id}:{cycle_no}"


@dataclass(frozen=True)
class TickOutcome:
    campaign_id: str
    action: str
    """opened | stopped | deadline_reached | budget_exhausted | blocked | skipped"""
    task_id: str | None = None
    cycle_no: int | None = None


class CampaignScheduler:
    """Async polling loop. ``start()`` / ``await stop()`` from the lifespan."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        manager: ConnectionManager | None = None,
        machine_bus: MachineBus | None = None,
        clock: Clock = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval = poll_interval_seconds
        self._manager = manager
        self._machine_bus = machine_bus
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        install_dispatcher(self.dispatch_eval)
        self._task = asyncio.create_task(self._run(), name="anygarden-campaign-scheduler")
        log.info("campaign_scheduler_started", extra={"interval": self._poll_interval})

    async def stop(self) -> None:
        uninstall_dispatcher(self.dispatch_eval)
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            log.warning("campaign_scheduler_stop_timeout")
            self._task.cancel()
        finally:
            self._task = None
            log.info("campaign_scheduler_stopped")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception:  # pragma: no cover — defensive
                log.exception("campaign_scheduler_tick_failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue
            else:
                break

    async def dispatch_eval(self, attempt_id: str) -> str:
        """Send one pending evaluation to its machine (after-commit hook)."""
        return await dispatch_eval(
            self._session_factory, self._machine_bus, attempt_id, clock=self._clock
        )

    async def reconcile(self, *, now: datetime | None = None) -> None:
        """Safety net before opening cycles (Phase 4 hooks react at once;
        this only catches what they missed): expire evaluation deadlines,
        close cycles whose run or Task already ended, stop campaigns of
        archived rooms, and re-send pending evaluations."""
        now = now or self._clock()
        clock: Clock = lambda: now
        async with self._session_factory() as db:
            await begin_write_transaction(db)
            await expire_eval_deadlines(db, clock=clock)
            await reconcile_open_cycles(db, clock=clock)
            await db.commit()
        await request_pending_evals(self._session_factory, self._machine_bus, clock=self._clock)

    async def _tick(self) -> list[TickOutcome]:
        now = self._clock()
        try:
            await self.reconcile(now=now)
        except Exception:  # pragma: no cover — defensive; opening still runs
            log.exception("campaign_reconcile_failed")
        async with self._session_factory() as db:
            due_ids = (await db.execute(
                select(Campaign.id)
                .where(
                    Campaign.status == "active",
                    or_(Campaign.next_cycle_at <= now, Campaign.deadline_at <= now),
                    ~_has_open_attempt(),
                )
                .order_by(Campaign.next_cycle_at.asc())
                .limit(MAX_CAMPAIGNS_PER_TICK)
            )).scalars().all()
        outcomes: list[TickOutcome] = []
        for campaign_id in due_ids:
            try:
                outcomes.append(await self.advance(campaign_id, now=now))
            except Exception:  # pragma: no cover — defensive, per campaign
                log.exception("campaign_advance_failed", extra={"campaign_id": campaign_id})
        return outcomes

    async def advance(self, campaign_id: str, *, now: datetime | None = None) -> TickOutcome:
        """Advance one campaign by at most one step, in its own transaction."""
        now = now or self._clock()
        clock: Clock = lambda: now
        async with self._session_factory() as db:
            # SQLite: take the writer lock before reading, so two ticks
            # serialise instead of failing a read→write upgrade.
            await begin_write_transaction(db)
            outcome = await self._advance(db, campaign_id, now=now, clock=clock)
            if outcome.action == "skipped":
                await db.rollback()
                return outcome
            try:
                await db.commit()
            except IntegrityError:
                # A concurrent writer opened the same cycle (Task key or
                # attempt number); theirs stands.
                await db.rollback()
                return TickOutcome(campaign_id, "skipped")
            return outcome

    async def _advance(
        self, db: AsyncSession, campaign_id: str, *, now: datetime, clock: Clock
    ) -> TickOutcome:
        campaign = await db.get(Campaign, campaign_id, populate_existing=True)
        skipped = TickOutcome(campaign_id, "skipped")
        if campaign is None or campaign.status != "active":
            return skipped
        open_attempt = await db.scalar(
            select(CampaignAttempt.id).where(
                CampaignAttempt.campaign_id == campaign_id,
                CampaignAttempt.closed_at.is_(None),
            ).limit(1)
        )
        if open_attempt is not None:
            return skipped

        room = await db.get(Room, campaign.room_id)
        if room is None or room.archived_at is not None:
            changed = await apply_transition(
                db, campaign, "stop", actor=SYSTEM_ACTOR, clock=clock,
                details={"reason": "room_archived"}, stop_reason="room_archived",
            )
            return TickOutcome(campaign_id, "stopped" if changed else "skipped")

        if campaign.deadline_at is not None and _aware(campaign.deadline_at) <= now:
            changed = await apply_transition(
                db, campaign, "deadline", actor=SYSTEM_ACTOR, clock=clock,
                stop_reason="deadline",
            )
            return TickOutcome(campaign_id, "deadline_reached" if changed else "skipped")

        if remaining_cycle_tokens(campaign) <= 0:
            changed = await apply_transition(
                db, campaign, "budget_exhausted", actor=SYSTEM_ACTOR, clock=clock,
                details={"tokens_used": campaign.tokens_used}, stop_reason="budget",
            )
            return TickOutcome(campaign_id, "budget_exhausted" if changed else "skipped")

        if campaign.next_cycle_at is None or _aware(campaign.next_cycle_at) > now:
            return skipped

        participant = (
            await find_agent_participant(db, room_id=room.id, agent_id=campaign.agent_id)
            if campaign.agent_id else None
        )
        if participant is None:
            changed = await apply_transition(
                db, campaign, "block", actor=SYSTEM_ACTOR, clock=clock,
                details={"reason": "agent_not_in_room"},
                status_detail="The campaign's agent is no longer a participant of the room.",
            )
            return TickOutcome(campaign_id, "blocked" if changed else "skipped")

        cycle_no = int(await db.scalar(
            select(func.coalesce(func.max(CampaignAttempt.cycle_no), 0))
            .where(CampaignAttempt.campaign_id == campaign_id)
        )) + 1
        attempts = list(await db.scalars(
            select(CampaignAttempt).where(CampaignAttempt.campaign_id == campaign_id)
        ))
        # Render before the claim: the claim consumes ``wake_reason`` (e.g.
        # the optimize-mode strategy-change flag the brief turns into a
        # directive).
        brief = render_brief(campaign, attempts, cycle_no=cycle_no, now=now)
        # The claim: only the writer that still sees this revision opens
        # the cycle.
        if not await cas_update(
            db, campaign, clock=clock, cycles_run=campaign.cycles_run + 1, wake_reason=None,
        ):
            return skipped
        task = await materialize_assigned_task(
            db,
            room=room,
            participant=participant,
            title=f"{campaign.title} — cycle {cycle_no}"[:500],
            spec=brief,
            created_by=campaign.owner_id,
            triggered_by="campaign",
            idempotency_key=cycle_idempotency_key(campaign_id, cycle_no),
            manager=self._manager,
            now=now,
            campaign_id=campaign_id,
        )
        db.add(CampaignAttempt(
            campaign_id=campaign_id,
            cycle_no=cycle_no,
            kind="cycle",
            task_id=task.id,
            opened_at=now,
        ))
        await db.flush()
        await record_event(
            db, campaign_id,
            key=f"campaign:{campaign_id}:cycle:{cycle_no}:opened",
            type="cycle_opened",
            details={"cycle_no": cycle_no, "task_id": task.id},
            actor=SYSTEM_ACTOR,
            clock=clock,
        )
        log.info(
            "campaign_cycle_opened",
            extra={"campaign_id": campaign_id, "cycle_no": cycle_no, "task_id": task.id},
        )
        return TickOutcome(campaign_id, "opened", task_id=task.id, cycle_no=cycle_no)


__all__ = [
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "MAX_CAMPAIGNS_PER_TICK",
    "CampaignScheduler",
    "TickOutcome",
    "cycle_idempotency_key",
]
