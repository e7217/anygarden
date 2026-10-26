"""Sending campaign evaluations to machines (#930 §3.7, Phase 3).

``lifecycle.request_eval`` marks an attempt ``pending`` with a persisted
``eval_job_id`` inside the caller's transaction. Sending happens after
that transaction commits:

- right away, through a dispatcher the scheduler installs at start-up
  (``install_dispatcher``) and that runs after the commit of any session
  that queued a request (``queue_dispatch_after_commit``),
- again on every scheduler tick for attempts still ``pending`` on an
  online machine (``request_pending_evals``) — a machine that was offline,
  busy with another evaluation or reconnecting is asked again,
- and when a machine registers (reconnect).

The request waits only for the machine's acknowledgement. An accepted job
becomes ``running`` and keeps its server-side deadline; the result frame
is matched by ``job_id`` (``handle_eval_result_frame``), so it is accepted
after a server restart too.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from anygarden.campaigns.service import Clock, utcnow
from anygarden.db.engine import begin_write_transaction
from anygarden.db.models import Agent, Campaign, CampaignAttempt

if TYPE_CHECKING:
    from anygarden.scheduler.machine_bus import MachineBus

log = logging.getLogger(__name__)

_QUEUE_KEY = "campaign_eval_dispatch"
_dispatcher: Callable[[str], Awaitable[Any]] | None = None
_background: set[asyncio.Task[Any]] = set()


# ── After-commit dispatch ───────────────────────────────────────────


def install_dispatcher(dispatch: Callable[[str], Awaitable[Any]]) -> None:
    global _dispatcher
    _dispatcher = dispatch


def uninstall_dispatcher(dispatch: Callable[[str], Awaitable[Any]] | None = None) -> None:
    global _dispatcher
    if dispatch is None or _dispatcher is dispatch:
        _dispatcher = None


def queue_dispatch_after_commit(db: AsyncSession, attempt_id: str) -> None:
    db.sync_session.info.setdefault(_QUEUE_KEY, set()).add(attempt_id)


@event.listens_for(Session, "after_commit")
def _dispatch_after_commit(session: Session) -> None:
    if session.in_nested_transaction():
        # ``after_commit`` also fires when a SAVEPOINT (``begin_nested``)
        # commits; wait for the outer transaction.
        return
    queued = session.info.pop(_QUEUE_KEY, None)
    if not queued or _dispatcher is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — sync use; the tick retries
        return
    for attempt_id in sorted(queued):
        task = loop.create_task(_dispatcher(attempt_id))
        _background.add(task)
        task.add_done_callback(_background.discard)


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    if session.in_nested_transaction():  # a SAVEPOINT rolled back; the outer one goes on
        return
    session.info.pop(_QUEUE_KEY, None)


# ── Requests ────────────────────────────────────────────────────────


def eval_request_frame(campaign: Campaign, attempt: CampaignAttempt) -> dict[str, Any]:
    evaluator = campaign.evaluator or {}
    return {
        "type": "campaign_eval_request",
        "job_id": attempt.eval_job_id,
        "agent_id": campaign.agent_id,
        "cwd": evaluator.get("cwd", "."),
        "command": evaluator["command"],
        "timeout_sec": int(evaluator.get("timeout_sec") or 600),
        "pin_paths": list(evaluator.get("pin_paths") or []),
        "expected_pins": campaign.evaluator_pins,
        "score_key": evaluator.get("score_key", "score"),
    }


async def dispatch_eval(
    session_factory: async_sessionmaker[AsyncSession],
    machine_bus: MachineBus | None,
    attempt_id: str,
    *,
    clock: Clock = utcnow,
) -> str:
    """Send one pending evaluation. Returns ``accepted`` or why it was not
    sent (``not_pending``, ``offline``, ``busy``, ``timeout``…); anything
    but ``accepted`` leaves the attempt pending for the next tick."""
    from anygarden.campaigns.lifecycle import EVAL_GRACE_SECONDS
    from anygarden.scheduler.machine_bus import MachineRequestError

    if machine_bus is None:
        return "offline"
    async with session_factory() as db:
        attempt = await db.get(CampaignAttempt, attempt_id)
        if (
            attempt is None or attempt.closed_at is not None
            or attempt.eval_status != "pending" or attempt.eval_job_id is None
        ):
            return "not_pending"
        campaign = await db.get(Campaign, attempt.campaign_id)
        agent = await db.get(Agent, campaign.agent_id) if campaign and campaign.agent_id else None
        machine_id = agent.placed_on_machine_id if agent is not None else None
        if machine_id is None or not machine_bus.is_connected(machine_id):
            return "offline"
        frame = eval_request_frame(campaign, attempt)
        timeout = frame["timeout_sec"]
    try:
        ack = await machine_bus.request_campaign_eval(machine_id, frame)
    except MachineRequestError as exc:
        return exc.reason
    if not ack.get("accepted"):
        return ack.get("reason") or "refused"
    async with session_factory() as db:
        await begin_write_transaction(db)
        attempt = await db.get(CampaignAttempt, attempt_id, populate_existing=True)
        if attempt is not None and attempt.closed_at is None and attempt.eval_status == "pending":
            now = clock()
            run_deadline = now + timedelta(seconds=timeout + EVAL_GRACE_SECONDS)
            attempt.eval_status = "running"
            attempt.eval_started_at = now
            attempt.eval_deadline_at = run_deadline
            from anygarden.campaigns.notify import mark

            mark(db, attempt_id=attempt.id)
            await db.commit()
        else:
            await db.rollback()
    log.info("campaign_eval_dispatched", extra={"attempt_id": attempt_id, "machine_id": machine_id})
    return "accepted"


async def request_pending_evals(
    session_factory: async_sessionmaker[AsyncSession],
    machine_bus: MachineBus | None,
    *,
    machine_id: str | None = None,
    clock: Clock = utcnow,
    limit: int = 25,
) -> dict[str, str]:
    """(Re)send every pending evaluation whose machine is online."""
    if machine_bus is None:
        return {}
    async with session_factory() as db:
        stmt = (
            select(CampaignAttempt.id)
            .join(Campaign, Campaign.id == CampaignAttempt.campaign_id)
            .join(Agent, Agent.id == Campaign.agent_id)
            .where(
                CampaignAttempt.closed_at.is_(None),
                CampaignAttempt.eval_status == "pending",
                CampaignAttempt.eval_job_id.is_not(None),
                Agent.placed_on_machine_id.is_not(None),
            )
            .order_by(CampaignAttempt.opened_at)
            .limit(limit)
        )
        if machine_id is not None:
            stmt = stmt.where(Agent.placed_on_machine_id == machine_id)
        ids = list(await db.scalars(stmt))
    results = {}
    for attempt_id in ids:
        try:
            results[attempt_id] = await dispatch_eval(
                session_factory, machine_bus, attempt_id, clock=clock
            )
        except Exception:  # pragma: no cover — defensive, per attempt
            log.exception("campaign_eval_dispatch_failed", extra={"attempt_id": attempt_id})
    return results


def request_pending_evals_soon(app: Any, machine_id: str) -> None:
    """Re-send pending evaluations after a machine registers.

    Runs in the background: the machine's acknowledgement arrives through
    the same frame loop that is handling the ``register`` frame.
    """
    session_factory = getattr(app.state, "session_factory", None)
    machine_bus = getattr(app.state, "machine_bus", None)
    if session_factory is None or machine_bus is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover
        return
    task = loop.create_task(
        request_pending_evals(session_factory, machine_bus, machine_id=machine_id)
    )
    _background.add(task)
    task.add_done_callback(_background.discard)


# ── Results ─────────────────────────────────────────────────────────


async def handle_eval_result_frame(
    session_factory: async_sessionmaker[AsyncSession],
    machine_id: str,
    data: dict[str, Any],
    *,
    clock: Clock = utcnow,
) -> str:
    """Validate and score a ``campaign_eval_result`` frame in its own
    write transaction."""
    from anygarden.campaigns.lifecycle import on_eval_result
    from anygarden.machine.protocol.frames import CampaignEvalResultFrame

    try:
        frame = CampaignEvalResultFrame.model_validate(data)
    except ValidationError:
        return "invalid_frame"
    async with session_factory() as db:
        await begin_write_transaction(db)
        outcome = await on_eval_result(db, frame.model_dump(), machine_id=machine_id, clock=clock)
        await db.commit()
    log.info("campaign_eval_result", extra={"job_id": frame.job_id, "outcome": outcome})
    return outcome


__all__ = [
    "dispatch_eval",
    "eval_request_frame",
    "handle_eval_result_frame",
    "install_dispatcher",
    "queue_dispatch_after_commit",
    "request_pending_evals",
    "request_pending_evals_soon",
    "uninstall_dispatcher",
]
