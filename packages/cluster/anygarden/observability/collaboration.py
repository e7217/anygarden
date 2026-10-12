"""Collaboration health metrics from the durable turn, task and usage tables.

One aggregation shared by the admin API (operational monitoring) and the
benchmark harness (per-run measurement on an isolated node), so a number
in a benchmark report means the same thing as the number on a live node.

What each figure answers:

- ``turns.completed_without_reply`` — the agent finished the turn but no
  message was accepted and it did not deliberately skip. This is the
  "request sent, nothing came back" silent-loss signal.
- ``turns.stuck`` — open turns (pending/leased/retrying/completing) whose
  last update is older than ``stuck_after``: work that is neither moving
  nor terminal.
- ``tasks.completion_rate`` — done / (done + failed) among tasks created
  in the window. ``blocked`` and open tasks are reported, not scored.
- ``cost.cost_per_done_task`` — reported USD cost divided by done tasks.
  Engines that report no cost contribute 0, so read it with
  ``cost.requests_without_cost``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import AgentTurn, Room, Task, UsageLedger
from anygarden.turns.service import OPEN_TURN_STATES

# The agent declined the turn on purpose (#720); not a lost reply.
SKIPPED_REASON = "agent_skipped"
DEFAULT_STUCK_AFTER = timedelta(minutes=15)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _scoped(stmt: Select, room_column: Any, room_ids: list[str] | None) -> Select:
    return stmt if room_ids is None else stmt.where(room_column.in_(room_ids))


async def project_room_ids(db: AsyncSession, project_id: str) -> list[str]:
    rows = await db.execute(select(Room.id).where(Room.project_id == project_id))
    return [room_id for (room_id,) in rows]


async def collect_collaboration_metrics(
    db: AsyncSession,
    *,
    since: datetime,
    room_ids: list[str] | None = None,
    now: datetime | None = None,
    stuck_after: timedelta = DEFAULT_STUCK_AFTER,
) -> dict[str, Any]:
    """Aggregate turn, task and cost health for rows created since ``since``.

    ``room_ids`` limits every figure to those rooms (``None`` = all rooms).
    """
    now = now or datetime.now(UTC)

    turn_rows = (
        await db.execute(
            _scoped(
                select(
                    AgentTurn.state,
                    AgentTurn.terminal_reason,
                    AgentTurn.accepted_message_id.is_not(None),
                    AgentTurn.retry_count,
                    AgentTurn.updated_at,
                ).where(AgentTurn.created_at >= since),
                AgentTurn.room_id,
                room_ids,
            )
        )
    ).all()
    by_state: dict[str, int] = {}
    replied = skipped = without_reply = retried = stuck = 0
    for state, reason, has_reply, retry_count, updated_at in turn_rows:
        by_state[state] = by_state.get(state, 0) + 1
        if retry_count:
            retried += 1
        if state in OPEN_TURN_STATES:
            if updated_at is not None and now - _aware(updated_at) > stuck_after:
                stuck += 1
            continue
        if state != "completed":
            continue
        if has_reply:
            replied += 1
        elif reason == SKIPPED_REASON:
            skipped += 1
        else:
            without_reply += 1
    total_turns = len(turn_rows)
    terminal_turns = sum(by_state.get(s, 0) for s in ("completed", "failed", "cancelled"))

    task_rows = (
        await db.execute(
            _scoped(
                select(Task.status, func.count()).where(Task.created_at >= since),
                Task.room_id,
                room_ids,
            ).group_by(Task.status)
        )
    ).all()
    tasks = {status: int(count) for status, count in task_rows}
    done, failed = tasks.get("done", 0), tasks.get("failed", 0)

    usage = (
        await db.execute(
            _scoped(
                select(
                    func.count(UsageLedger.id),
                    func.coalesce(func.sum(UsageLedger.prompt_tokens), 0),
                    func.coalesce(func.sum(UsageLedger.completion_tokens), 0),
                    func.coalesce(func.sum(UsageLedger.cost_usd), 0.0),
                    func.count(UsageLedger.id).filter(UsageLedger.cost_usd.is_(None)),
                ).where(UsageLedger.timestamp >= since),
                UsageLedger.room_id,
                room_ids,
            )
        )
    ).one()
    requests, prompt_tokens, completion_tokens, cost_usd, without_cost = usage
    cost_usd = round(float(cost_usd), 6)

    return {
        "since": since.isoformat(),
        "until": now.isoformat(),
        "room_count": None if room_ids is None else len(room_ids),
        "turns": {
            "total": total_turns,
            "by_state": by_state,
            "replied": replied,
            "skipped": skipped,
            "completed_without_reply": without_reply,
            "retried": retried,
            "stuck": stuck,
            "failure_rate": _ratio(by_state.get("failed", 0), terminal_turns),
            "silent_loss_rate": _ratio(without_reply, replied + without_reply),
        },
        "tasks": {
            "total": sum(tasks.values()),
            "by_status": tasks,
            "completion_rate": _ratio(done, done + failed),
        },
        "cost": {
            "requests": int(requests),
            "requests_without_cost": int(without_cost),
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "cost_usd": cost_usd,
            "cost_per_done_task": round(cost_usd / done, 6) if done else None,
        },
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
