"""Closing campaign cycles (#930 §3.2–§3.3, Phase 3–4).

Every hook here runs **inside the caller's transaction** and never commits:

- ``on_execution_terminal`` — a cycle's project execution ended
  (``project_executions.service._event``). Adds the run's tokens; a
  completed run asks for an evaluation, a cancelled / limit-stopped /
  deadline-stopped run closes the cycle without one.
- ``on_eval_result`` — the machine reported the evaluation. Classifies
  the outcome (``judge.classify``), updates the best attempt and the
  counters, and drives the state table.
- ``expire_eval_deadlines`` — the server-side bound on waiting for a
  result: past ``eval_deadline_at`` the cycle closes as ``eval_failed``.
- ``on_cycle_task_closed`` — the cycle Task ended from outside (the
  stuck-task sweeper) before its run was evaluated: closes the cycle as
  ``unknown``. The cycle is never re-run (CMP-11).
- ``stop_campaign`` / ``stop_room_campaigns`` — a stop (and a room
  archive, CMP-12) closes the open cycle in the same transaction.
- ``reconcile_open_cycles`` — the scheduler tick's safety net for a hook
  that did not fire.

Invariant (plan decision 7): when a cycle closes, its execution and its
cycle Task end terminal. A still-active execution is cancelled through the
project-execution stop protocol (a system ``ExecutionMutation`` with exact
stop receipts — the same path a usage limit uses), never by writing its
status. Its cycle Task becomes ``failed`` when the execution's terminal
event arrives (``on_execution_terminal`` on an already closed attempt); a
cycle Task without an execution is failed directly.

Every close sets ``next_cycle_at`` (the scheduler only checks that no
cycle is open), so the next cycle waits ``MIN_GAP_SECONDS``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.campaigns.brief import STRATEGY_CHANGE_REASON
from anygarden.campaigns.judge import JUDGED_OUTCOMES, Verdict, classify, reaches_target
from anygarden.campaigns.notify import mark
from anygarden.campaigns.policy import (
    DEFAULT_EVAL_TIMEOUT_SECONDS,
    EVAL_FAILURE_BLOCK_THRESHOLD,
    PLATEAU_WINDOW,
    RETRO_EVERY_CYCLES,
    TERMINAL_STATUSES,
    cycle_tokens_spent,
    evaluator_digest,
)
from anygarden.campaigns.service import (
    Clock,
    IllegalTransition,
    apply_transition,
    cas_update,
    cycle_closed_at,
    next_cycle_at,
    record_event,
    transition,
    utcnow,
)
from anygarden.db.models import (
    Agent,
    Campaign,
    CampaignAttempt,
    ExecutionMutation,
    ProjectExecution,
    Room,
    Task,
)
from anygarden.tasks_status import TERMINAL_STATUSES as TASK_TERMINAL_STATUSES

SYSTEM_ACTOR = "system:campaign"
EVAL_GRACE_SECONDS = 120
"""Slack on top of the evaluator timeout before the server gives up."""
EVAL_PENDING_WINDOW_SECONDS = 24 * 3600
"""How long an evaluation may wait for a machine to accept it (agent not
placed, machine offline or busy, a request the machine cannot parse)
before the cycle closes as ``eval_failed``."""
STRATEGY_CHANGE = STRATEGY_CHANGE_REASON
"""``Campaign.wake_reason`` flag: the next brief asks for a new strategy."""
CYCLE_CLOSED_REASON = "CAMPAIGN_CYCLE_CLOSED"

TERMINAL_EXECUTION_EVENTS: frozenset[str] = frozenset({
    "execution_completed",
    "execution_usage_limit_stopped",
    "execution_deadline_stopped",
    "execution_cancelled",
})
_CANCELLABLE_EXECUTION_STATES = frozenset({"planning", "running", "waiting_children", "revising"})
_OPEN_EXECUTION_STATES = _CANCELLABLE_EXECUTION_STATES | {"cancelling"}
_OPEN_TASK_STATES = ("todo", "in_progress", "blocked")
_EXCERPT_CHARS = 4096


def _legal(status: str, event: str) -> bool:
    try:
        transition(status, event)
    except IllegalTransition:
        return False
    return True


async def _campaign(db: AsyncSession, campaign_id: str) -> Campaign | None:
    return await db.get(Campaign, campaign_id, populate_existing=True)


Plan = Callable[[Campaign], tuple[str | None, dict[str, Any], dict[str, Any]]]


async def _write(
    db: AsyncSession, campaign: Campaign, plan: Plan, *, actor: str, clock: Clock
) -> bool:
    """Apply *plan* (``campaign -> (event | None, values, details)``) under
    CAS, re-reading and re-planning if another writer moved the row."""
    for _ in range(3):
        event, values, details = plan(campaign)
        if event is None:
            if not values:
                return True
            ok = await cas_update(db, campaign, clock=clock, **values)
        else:
            ok = await apply_transition(
                db, campaign, event, actor=actor, clock=clock, details=details, **values
            )
        if ok:
            return True
        await db.refresh(campaign)
    return False


# ── Attempt helpers ─────────────────────────────────────────────────


async def _close(
    db: AsyncSession,
    attempt: CampaignAttempt,
    *,
    outcome: str | None,
    eval_status: str,
    clock: Clock,
    finished_at: datetime | None = None,
    **values: Any,
) -> bool:
    """Close *attempt* once. Returns ``False`` if it was already closed."""
    closed_at = cycle_closed_at(finished_at=finished_at, opened_at=attempt.opened_at, clock=clock)
    result = await db.execute(
        update(CampaignAttempt)
        .where(CampaignAttempt.id == attempt.id, CampaignAttempt.closed_at.is_(None))
        .values(closed_at=closed_at, outcome=outcome, eval_status=eval_status, **values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    attempt.closed_at = closed_at
    attempt.outcome = outcome
    attempt.eval_status = eval_status
    for name, value in values.items():
        setattr(attempt, name, value)
    mark(db, attempt_id=attempt.id)
    return True


async def _sync_tokens(db: AsyncSession, attempt: CampaignAttempt) -> int:
    """Copy the run's measured tokens onto the attempt; return the increase
    the campaign's ``tokens_used`` still has to absorb.

    Runs at the run's terminal event, when the cycle closes and when the
    engine's usage is measured (``on_usage_settled``, #956) — usually last."""
    if attempt.execution_id is None:
        return 0
    from anygarden.project_executions.usage import execution_usage_summary

    try:
        summary = await execution_usage_summary(db, execution_id=attempt.execution_id)
    except ValueError:
        return 0
    total = int(summary.get("known_total_tokens") or 0)
    seen = int(attempt.tokens or 0)
    if total <= seen:
        return 0
    attempt.tokens = total
    await db.flush()
    return total - seen


async def _fail_cycle_task(db: AsyncSession, task_id: str, *, reason: str, clock: Clock) -> None:
    result = await db.execute(
        update(Task)
        .where(Task.id == task_id, Task.status.in_(_OPEN_TASK_STATES))
        .values(status="failed", error=reason[:500], finished_at=clock())
        .execution_options(synchronize_session=False)
    )
    # Keep an already loaded Task object in step with the row.
    await db.get(Task, task_id, populate_existing=True)
    if result.rowcount:
        # The room's task list and the agent profile follow ``task.updated``
        # (published after the commit, see ``campaigns.notify``).
        mark(db, task_id=task_id)


async def cancel_cycle_execution(
    db: AsyncSession, execution: ProjectExecution, *, reason: str = CYCLE_CLOSED_REASON
) -> bool:
    """Request a system cancel of a cycle's still-active execution.

    Same protocol as a user cancel (``project_executions.mutations``) and a
    usage limit (``limits.close_execution_limit``): fence the turns, record
    exact stops and let ``settle_execution_mutation`` finish the run once
    every stop is confirmed. Caller owns the commit.
    """
    from anygarden.project_executions.service import _event
    from anygarden.project_executions.stop_service import (
        fence_execution_turns,
        settle_execution_mutation,
    )

    execution = await db.get(ProjectExecution, execution.id, populate_existing=True)
    if execution is None or execution.status not in _CANCELLABLE_EXECUTION_STATES:
        return False
    now = utcnow()
    changed = await db.scalar(
        update(ProjectExecution)
        .where(
            ProjectExecution.id == execution.id,
            ProjectExecution.input_revision == execution.input_revision,
            ProjectExecution.state_revision == execution.state_revision,
            ProjectExecution.status.in_(_CANCELLABLE_EXECUTION_STATES),
        )
        .values(status="cancelling", state_revision=ProjectExecution.state_revision + 1,
                updated_at=now)
        .returning(ProjectExecution.id)
    )
    if changed is None:
        return False
    await db.refresh(execution)
    await db.execute(
        update(ExecutionMutation)
        .where(ExecutionMutation.execution_id == execution.id,
               ExecutionMutation.phase == "awaiting_stop")
        .values(phase="superseded", completed_at=now)
    )
    identity = {"execution_id": execution.id, "input_revision": execution.input_revision,
                "reason_code": reason}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    mutation = ExecutionMutation(
        execution_id=execution.id,
        operation_id=str(uuid5(NAMESPACE_URL, "anygarden:campaign-cycle-close:" + encoded)),
        request_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        action="cancel", previous_input_revision=execution.input_revision,
        input_revision=execution.input_revision,
        expected_state_revision=execution.state_revision - 1,
        requested_by_user_id=None, root_task_id=execution.root_task_id,
        reason=reason, requested_at=now,
    )
    db.add(mutation)
    await db.flush()
    await _event(db, execution, f"mutation:{mutation.id}:requested", "execution_cancel_requested",
                 task_id=execution.root_task_id,
                 details={"mutation_id": mutation.id, "input_revision": execution.input_revision,
                          "reason": reason, "system_origin": "campaign"})
    await fence_execution_turns(db, execution=execution, mutation=mutation)
    await settle_execution_mutation(db, execution.id)
    await db.flush()
    return True


async def _settle_cycle_work(
    db: AsyncSession, attempt: CampaignAttempt, *, reason: str, clock: Clock
) -> None:
    """Enforce the close invariant for *attempt*'s execution and Task."""
    if attempt.execution_id is not None:
        execution = await db.get(ProjectExecution, attempt.execution_id, populate_existing=True)
        if execution is not None and execution.status in _CANCELLABLE_EXECUTION_STATES:
            await cancel_cycle_execution(db, execution)
            execution = await db.get(ProjectExecution, attempt.execution_id, populate_existing=True)
        if execution is not None and execution.status in _OPEN_EXECUTION_STATES:
            # Waiting for exact stop receipts; the execution's terminal
            # event finishes the cycle Task (on_execution_terminal).
            return
    if attempt.task_id is not None:
        await _fail_cycle_task(db, attempt.task_id, reason=reason, clock=clock)


# ── Judging and state ───────────────────────────────────────────────


async def _after_close(
    db: AsyncSession,
    campaign: Campaign,
    attempt: CampaignAttempt,
    *,
    verdict: Verdict | None,
    token_delta: int,
    clock: Clock,
    pins: dict[str, str] | None = None,
    actor: str = SYSTEM_ACTOR,
) -> None:
    """Fold a closed cycle into the campaign: tokens, best attempt,
    counters, the next status and ``next_cycle_at``.

    The agent's requests for this cycle apply here: ``wake_at``
    (``schedule_campaign_wakeup``) pushes the next cycle back, and
    ``block_reason`` (``report_campaign_blocked``) blocks the campaign
    unless the verdict already moved it (a target, the budget, an
    evaluator problem). A closed work cycle that completes a block of
    ``RETRO_EVERY_CYCLES`` cycles makes a retrospective due.
    """
    strategy_change = False
    retrospective = attempt.kind == "retrospective"

    def plan(c: Campaign) -> tuple[str | None, dict[str, Any], dict[str, Any]]:
        nonlocal strategy_change
        strategy_change = False
        values: dict[str, Any] = {}
        details: dict[str, Any] = {"cycle_no": attempt.cycle_no, "outcome": attempt.outcome}
        tokens_used = int(c.tokens_used or 0) + token_delta
        if token_delta:
            values["tokens_used"] = tokens_used
        if pins and c.evaluator_pins is None:
            values["evaluator_pins"] = pins
        if c.status in TERMINAL_STATUSES:
            return None, values, details
        values["next_cycle_at"] = next_cycle_at(attempt.closed_at, attempt.wake_at)
        if (
            not retrospective and c.retro_due is None and c.cycles_run > 0
            and c.cycles_run % RETRO_EVERY_CYCLES == 0
        ):
            values["retro_due"] = "periodic"
        event: str | None = None
        if verdict is not None:
            failures = int(c.consecutive_eval_failures or 0)
            failures = failures + 1 if verdict.eval_failed else (
                failures if verdict.pin_mismatch else 0
            )
            values["consecutive_eval_failures"] = failures
            since = int(c.cycles_since_improvement or 0)
            if verdict.outcome in JUDGED_OUTCOMES or verdict.outcome == "no_record":
                since = 0 if verdict.improved else since + 1
            if verdict.improved:
                values["best_attempt_id"] = attempt.id
            if verdict.pin_mismatch:
                event = "block"
                values["status_detail"] = (
                    "The pinned evaluator files changed; restore them or update the pins."
                )
                details["reason"] = "pin_mismatch"
            elif failures >= EVAL_FAILURE_BLOCK_THRESHOLD:
                event = "block"
                values["status_detail"] = (
                    f"The evaluation failed {failures} times in a row; check the evaluator."
                )
                details["reason"] = "eval_failures"
            elif (
                c.mode == "target" and verdict.valid and verdict.outcome != "no_record"
                and reaches_target(verdict.dev_score, c.metric)
            ):
                event = "target_reached"
                details["dev_score"] = verdict.dev_score
                details["awaiting"] = (
                    "trusted_score" if c.trusted_eval == "external" else "confirmation"
                )
            elif cycle_tokens_spent(c.budget, tokens_used):
                event = "budget_exhausted"
            elif since >= PLATEAU_WINDOW and c.mode == "target":
                event = "plateau"
            elif since >= PLATEAU_WINDOW:
                # Optimize mode never stops on a plateau: ask for a change
                # of strategy in the next brief and start counting again.
                since = 0
                strategy_change = True
                values["wake_reason"] = STRATEGY_CHANGE
            values["cycles_since_improvement"] = since
        elif cycle_tokens_spent(c.budget, tokens_used):
            event = "budget_exhausted"
        if attempt.block_reason and event in {None, "plateau"}:
            event = "block"
            hint = f" To unblock: {attempt.unblock_hint}" if attempt.unblock_hint else ""
            values["status_detail"] = f"The agent reported: {attempt.block_reason}.{hint}"[:4000]
            details["reason"] = "agent_reported"
            if strategy_change:
                strategy_change = False
                values.pop("wake_reason", None)
        if event == "budget_exhausted":
            values["stop_reason"] = "budget"
            details["tokens_used"] = tokens_used
        if event is not None and not _legal(c.status, event):
            event = None
        return event, values, details

    await _write(db, campaign, plan, actor=actor, clock=clock)
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:cycle:{attempt.cycle_no}:closed",
        type="cycle_closed",
        details={"cycle_no": attempt.cycle_no, "outcome": attempt.outcome,
                 "eval_status": attempt.eval_status, "dev_score": attempt.dev_score,
                 "tokens": attempt.tokens},
        actor=actor, clock=clock,
    )
    if (
        not retrospective and campaign.status == "active" and campaign.cycles_run > 0
        and campaign.cycles_run % RETRO_EVERY_CYCLES == 0
    ):
        # #964 — a progress report every block of cycles; the loop goes on.
        from anygarden.campaigns.report import request_report

        await request_report(db, campaign, reason="progress", previous="active")
    if strategy_change:
        await record_event(
            db, campaign.id,
            key=f"campaign:{campaign.id}:cycle:{attempt.cycle_no}:strategy_change",
            type="strategy_change",
            details={"cycle_no": attempt.cycle_no, "window": PLATEAU_WINDOW},
            actor=actor, clock=clock,
        )


async def _add_tokens(db: AsyncSession, campaign: Campaign, delta: int, *, clock: Clock) -> None:
    if delta <= 0:
        return

    def plan(c: Campaign):
        return None, {"tokens_used": int(c.tokens_used or 0) + delta}, {}

    await _write(db, campaign, plan, actor=SYSTEM_ACTOR, clock=clock)


# ── Hooks ───────────────────────────────────────────────────────────


async def request_eval(
    db: AsyncSession, campaign: Campaign, attempt: CampaignAttempt, *, clock: Clock = utcnow
) -> None:
    """Mark *attempt* as waiting for its evaluation and queue the request.

    ``eval_job_id`` (= the attempt id) is persisted so a result that
    arrives after a server restart still finds its attempt. While the
    agent's machine is offline or busy the attempt stays ``pending`` — the
    agent cannot run a next cycle there either — but not forever: the
    outer deadline is the evaluator timeout plus
    ``EVAL_PENDING_WINDOW_SECONDS``. Once the machine accepts the job
    (``evaluation.dispatch_eval``) the deadline becomes the run's own.
    """
    from anygarden.campaigns.evaluation import queue_dispatch_after_commit

    timeout = int((campaign.evaluator or {}).get("timeout_sec") or DEFAULT_EVAL_TIMEOUT_SECONDS)
    attempt.eval_status = "pending"
    attempt.eval_job_id = attempt.id
    attempt.eval_deadline_at = clock() + timedelta(
        seconds=timeout + EVAL_GRACE_SECONDS + EVAL_PENDING_WINDOW_SECONDS
    )
    await db.flush()
    mark(db, attempt_id=attempt.id)
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:cycle:{attempt.cycle_no}:eval_requested",
        type="eval_requested",
        details={"cycle_no": attempt.cycle_no, "job_id": attempt.eval_job_id},
        actor=SYSTEM_ACTOR, clock=clock,
    )
    queue_dispatch_after_commit(db, attempt.id)


async def on_usage_settled(
    db: AsyncSession, execution_id: str, *, clock: Clock = utcnow
) -> None:
    """A run's engine usage was measured (#956).

    The measurement arrives after the run's terminal event and usually
    after the evaluation closed the cycle, so the goal counts it here too.
    ``_sync_tokens`` adds only the increase, so a replay adds nothing; a
    spent budget stops the goal at the next scheduler tick."""
    attempt = await db.scalar(
        select(CampaignAttempt)
        .where(CampaignAttempt.execution_id == execution_id)
        .order_by(CampaignAttempt.cycle_no.desc())
        .limit(1)
    )
    if attempt is None:
        return
    campaign = await _campaign(db, attempt.campaign_id)
    if campaign is None:
        return
    await _add_tokens(db, campaign, await _sync_tokens(db, attempt), clock=clock)


async def on_execution_terminal(
    db: AsyncSession,
    execution: ProjectExecution,
    event_type: str,
    *,
    clock: Clock = utcnow,
) -> None:
    """A cycle execution reached a terminal event (see module docstring)."""
    if event_type not in TERMINAL_EXECUTION_EVENTS:
        return
    attempt = await db.scalar(
        select(CampaignAttempt)
        .where(CampaignAttempt.execution_id == execution.id)
        .order_by(CampaignAttempt.cycle_no.desc())
        .limit(1)
    )
    if attempt is None:
        return
    campaign = await _campaign(db, attempt.campaign_id)
    if campaign is None:
        return
    delta = await _sync_tokens(db, attempt)
    if attempt.closed_at is not None or attempt.eval_job_id is not None:
        # Closed earlier (stop, archive, unknown) or already evaluating:
        # count the tokens and finish the cycle Task if it is still open.
        if attempt.closed_at is not None:
            await _settle_cycle_work(db, attempt, reason=event_type, clock=clock)
        await _add_tokens(db, campaign, delta, clock=clock)
        return
    finished_at = getattr(execution, "finished_at", None)
    if attempt.kind == "retrospective":
        # A retrospective is not scored: it ends with its run. Its changes
        # are already recorded (``apply_campaign_learning``).
        completed = event_type == "execution_completed"
        if not await _close(db, attempt, outcome=None if completed else "unknown",
                            eval_status="skipped", clock=clock, finished_at=finished_at):
            return
        await _settle_cycle_work(db, attempt, reason=event_type, clock=clock)
        await _after_close(db, campaign, attempt, verdict=None, token_delta=delta, clock=clock)
        return
    if event_type == "execution_completed" and campaign.status not in TERMINAL_STATUSES:
        await request_eval(db, campaign, attempt, clock=clock)
        await _add_tokens(db, campaign, delta, clock=clock)
        return
    if not await _close(db, attempt, outcome="unknown", eval_status="skipped", clock=clock,
                        finished_at=finished_at):
        return
    await _settle_cycle_work(db, attempt, reason=event_type, clock=clock)
    await _after_close(db, campaign, attempt, verdict=None, token_delta=delta, clock=clock)


async def on_eval_result(
    db: AsyncSession,
    frame: dict[str, Any],
    *,
    machine_id: str | None = None,
    clock: Clock = utcnow,
) -> str:
    """Score a machine evaluation result. Returns what happened:
    ``judged`` | ``recorded`` (campaign already ended) | ``late`` |
    ``unknown_job`` | ``wrong_machine``."""
    job_id = frame.get("job_id")
    attempt = await db.scalar(
        select(CampaignAttempt).where(CampaignAttempt.eval_job_id == job_id)
    ) if job_id else None
    if attempt is None:
        return "unknown_job"
    campaign = await _campaign(db, attempt.campaign_id)
    if campaign is None:
        return "unknown_job"
    if machine_id is not None:
        agent = await db.get(Agent, campaign.agent_id) if campaign.agent_id else None
        if (
            agent is None or agent.placed_on_machine_id != machine_id
            or frame.get("agent_id") != campaign.agent_id
        ):
            return "wrong_machine"
    status = frame.get("status")
    parsed = frame.get("parsed")
    evaluator = campaign.evaluator or {}
    score_key = evaluator.get("score_key", "score")
    if attempt.closed_at is not None or attempt.eval_status not in {"pending", "running"}:
        # Authority belongs to the current, open cycle only; a result that
        # missed its deadline (or a stopped cycle) is kept as history.
        await record_event(
            db, campaign.id,
            key=f"campaign:{campaign.id}:job:{job_id}:late",
            type="late_eval_result",
            details={"cycle_no": attempt.cycle_no, "status": status,
                     "score": (parsed or {}).get(score_key)},
            actor=SYSTEM_ACTOR, clock=clock,
        )
        return "late"
    best = await db.get(CampaignAttempt, campaign.best_attempt_id) if campaign.best_attempt_id else None
    verdict = classify(
        metric=campaign.metric, score_key=score_key, status=status, parsed=parsed,
        best_score=best.dev_score if best is not None else None,
    )
    if attempt.recorded_at is None and verdict.outcome in JUDGED_OUTCOMES:
        # Decision 7 — an attempt counts only with the agent's one explicit
        # report. The score is kept, but an unreported run neither becomes
        # the best nor reaches the target; it counts towards the plateau
        # window and the next brief warns about the missing record.
        verdict = Verdict("no_record", verdict.eval_status, verdict.dev_score, verdict.valid)
    now = clock()
    excerpt = (frame.get("stdout_tail") or "")[-_EXCERPT_CHARS // 2:]
    stderr = (frame.get("stderr_tail") or "")[-_EXCERPT_CHARS // 2:]
    if stderr:
        excerpt = f"{excerpt}\n--- stderr ---\n{stderr}"
    if frame.get("error"):
        excerpt = f"[{frame['error']}] {excerpt}"
    if not await _close(
        db, attempt, outcome=verdict.outcome, eval_status=verdict.eval_status, clock=clock,
        finished_at=now, dev_score=verdict.dev_score, valid=verdict.valid,
        metrics=parsed if isinstance(parsed, dict) else None,
        eval_excerpt=excerpt[-_EXCERPT_CHARS:], eval_finished_at=now,
    ):
        return "late"
    delta = await _sync_tokens(db, attempt)
    await _settle_cycle_work(db, attempt, reason=CYCLE_CLOSED_REASON, clock=clock)
    ended = campaign.status in TERMINAL_STATUSES
    # Pins describe the evaluator the job ran; adopt them only while that
    # is still the campaign's evaluator (an edit re-pins at the next run).
    same_evaluator = attempt.eval_evaluator_digest == evaluator_digest(campaign.evaluator)
    await _after_close(
        db, campaign, attempt, verdict=None if ended else verdict, token_delta=delta,
        clock=clock,
        pins=frame.get("pins") if status != "invalid" and same_evaluator else None,
    )
    return "recorded" if ended else "judged"


async def expire_eval_deadlines(db: AsyncSession, *, clock: Clock = utcnow) -> list[str]:
    """Close cycles whose evaluation did not report in time as
    ``eval_failed`` (CMP-04). Returns the attempt ids."""
    now = clock()
    rows = list(await db.scalars(
        select(CampaignAttempt).where(
            CampaignAttempt.closed_at.is_(None),
            CampaignAttempt.eval_job_id.is_not(None),
            CampaignAttempt.eval_status.in_(("pending", "running")),
            CampaignAttempt.eval_deadline_at <= now,
        )
    ))
    expired = []
    for attempt in rows:
        campaign = await _campaign(db, attempt.campaign_id)
        if campaign is None:
            continue
        verdict = Verdict("eval_failed", "timed_out")
        stage = attempt.eval_status
        if stage == "pending":
            excerpt = "[server] the machine never accepted the evaluation before the deadline"
            if attempt.eval_excerpt:
                excerpt = f"{excerpt}; {attempt.eval_excerpt}"
        else:
            excerpt = "[server] no evaluation result before the deadline"
        if not await _close(db, attempt, outcome=verdict.outcome, eval_status="timed_out",
                            clock=clock, finished_at=now, eval_finished_at=now,
                            eval_excerpt=excerpt[:_EXCERPT_CHARS]):
            continue
        await record_event(
            db, campaign.id,
            key=f"campaign:{campaign.id}:cycle:{attempt.cycle_no}:eval_timed_out",
            type="eval_timed_out",
            details={"cycle_no": attempt.cycle_no, "job_id": attempt.eval_job_id,
                     "stage": stage},
            actor=SYSTEM_ACTOR, clock=clock,
        )
        delta = await _sync_tokens(db, attempt)
        await _settle_cycle_work(db, attempt, reason=CYCLE_CLOSED_REASON, clock=clock)
        ended = campaign.status in TERMINAL_STATUSES
        await _after_close(db, campaign, attempt, verdict=None if ended else verdict,
                           token_delta=delta, clock=clock)
        expired.append(attempt.id)
    return expired


async def _close_unknown(
    db: AsyncSession, attempt: CampaignAttempt, *, reason: str, clock: Clock,
    finished_at: datetime | None = None,
) -> bool:
    campaign = await _campaign(db, attempt.campaign_id)
    if campaign is None:
        return False
    delta = await _sync_tokens(db, attempt)
    if not await _close(db, attempt, outcome="unknown", eval_status="skipped", clock=clock,
                        finished_at=finished_at):
        return False
    await record_event(
        db, campaign.id,
        key=f"campaign:{campaign.id}:cycle:{attempt.cycle_no}:unknown",
        type="cycle_unknown",
        details={"cycle_no": attempt.cycle_no, "reason": reason},
        actor=SYSTEM_ACTOR, clock=clock,
    )
    await _settle_cycle_work(db, attempt, reason=reason, clock=clock)
    await _after_close(db, campaign, attempt, verdict=None, token_delta=delta, clock=clock)
    return True


async def on_cycle_task_closed(
    db: AsyncSession, task: Task, *, reason: str, clock: Clock = utcnow
) -> bool:
    """The cycle Task ended from outside before its run was evaluated:
    close the cycle as ``unknown`` (CMP-11). No-op for other tasks and for
    a cycle already waiting for its evaluation."""
    if task.campaign_id is None:
        return False
    attempt = await db.scalar(
        select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == task.campaign_id,
            CampaignAttempt.task_id == task.id,
            CampaignAttempt.closed_at.is_(None),
        )
    )
    if attempt is None or attempt.eval_job_id is not None:
        return False
    return await _close_unknown(db, attempt, reason=reason, clock=clock,
                                finished_at=task.finished_at)


async def stop_campaign(
    db: AsyncSession,
    campaign: Campaign,
    *,
    actor: str,
    stop_reason: str,
    reason: str | None = None,
    clock: Clock = utcnow,
) -> bool:
    """Stop an open campaign and close its open cycle in the same
    transaction. Returns ``False`` if it had already ended."""
    if campaign.status in TERMINAL_STATUSES:
        return False

    def plan(c: Campaign):
        if not _legal(c.status, "stop"):
            return None, {}, {}
        return "stop", {"stop_reason": stop_reason}, {"reason": reason or stop_reason}

    await _write(db, campaign, plan, actor=actor, clock=clock)
    if campaign.status != "stopped":
        return False
    for attempt in list(await db.scalars(
        select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == campaign.id,
            CampaignAttempt.closed_at.is_(None),
        )
    )):
        await _close_unknown(db, attempt, reason=f"campaign_stopped:{stop_reason}", clock=clock)
    return True


async def stop_room_campaigns(
    db: AsyncSession, room_id: str, *, actor: str, clock: Clock = utcnow
) -> list[str]:
    """CMP-12 — stop every open campaign of an archived room."""
    stopped = []
    for campaign in list(await db.scalars(
        select(Campaign).where(
            Campaign.room_id == room_id,
            Campaign.status.not_in(tuple(TERMINAL_STATUSES)),
        )
    )):
        if await stop_campaign(db, campaign, actor=actor, stop_reason="room_archived",
                               clock=clock):
            stopped.append(campaign.id)
    return stopped


async def reconcile_open_cycles(db: AsyncSession, *, clock: Clock = utcnow) -> int:
    """Safety net for hooks that did not fire. Returns cycles changed.

    - an open campaign in an archived room is stopped (with its cycle),
    - an open cycle whose execution already ended is closed as if its
      terminal event had arrived,
    - an open cycle whose Task ended, disappeared or lost its assignee
      before any execution is closed as ``unknown``.
    """
    changed = 0
    archived = list(await db.scalars(
        select(Campaign).join(Room, Room.id == Campaign.room_id).where(
            Room.archived_at.is_not(None),
            Campaign.status.not_in(tuple(TERMINAL_STATUSES)),
        )
    ))
    for campaign in archived:
        if await stop_campaign(db, campaign, actor=SYSTEM_ACTOR, stop_reason="room_archived",
                               clock=clock):
            changed += 1
    attempts = list(await db.scalars(
        select(CampaignAttempt).where(
            CampaignAttempt.closed_at.is_(None),
            CampaignAttempt.eval_job_id.is_(None),
        )
    ))
    for attempt in attempts:
        if attempt.execution_id is not None:
            execution = await db.get(ProjectExecution, attempt.execution_id, populate_existing=True)
            if execution is None or execution.status in _OPEN_EXECUTION_STATES:
                continue
            event = "execution_completed" if execution.status == "completed" else "execution_cancelled"
            await on_execution_terminal(db, execution, event, clock=clock)
            changed += 1
            continue
        task = await db.get(Task, attempt.task_id, populate_existing=True) if attempt.task_id else None
        if task is None:
            reason = "cycle_task_deleted"
        elif task.status in TASK_TERMINAL_STATUSES:
            reason = "cycle_task_closed"
        elif task.assignee_participant_id is None:
            # The lead left the room and its reservation was released; the
            # next tick blocks the campaign on the missing participant.
            reason = "cycle_task_unassigned"
        else:
            continue
        if await _close_unknown(
            db, attempt, reason=reason, clock=clock,
            finished_at=task.finished_at if task else None,
        ):
            changed += 1
    return changed


def cycle_task_is_open(task: Task) -> bool:
    """A campaign cycle Task that has not ended (generic Task API refuses
    to change or delete it — use the campaign's stop instead)."""
    return task.campaign_id is not None and task.status not in TASK_TERMINAL_STATUSES


__all__ = [
    "EVAL_GRACE_SECONDS",
    "EVAL_PENDING_WINDOW_SECONDS",
    "STRATEGY_CHANGE",
    "TERMINAL_EXECUTION_EVENTS",
    "cancel_cycle_execution",
    "cycle_task_is_open",
    "expire_eval_deadlines",
    "on_cycle_task_closed",
    "on_eval_result",
    "on_execution_terminal",
    "on_usage_settled",
    "reconcile_open_cycles",
    "request_eval",
    "stop_campaign",
    "stop_room_campaigns",
]
