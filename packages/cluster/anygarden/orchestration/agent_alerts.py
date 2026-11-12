"""What needs the person, per agent, across the rooms they can open (#1032).

The overview map puts these on each agent's circle. Nothing is stored here:
every alert is read from the rows that cause it, so it goes away with them.

- failed_tasks: tasks assigned to the agent that failed and were not requeued.
- failed_rooms: rooms where the agent's latest finished turn failed. A later
  completed turn in the same room clears it; a cancelled one does not.
- waiting: requests from the agent waiting on a person — workspace action
  approvals still open, and project approvals and questions from the inbox.
- unread_rooms: rooms with a finished answer of the agent the person has not
  read yet (skipped turns do not count).
- queued_tasks: tasks assigned to the agent that have not started.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import AgentTurn, Message, Participant, Task
from anygarden.db.workspace_action_models import WorkspaceActionRequest

# The overview only needs a few of each to link to; counts stay exact.
LIST_LIMIT = 5


def _empty() -> dict[str, Any]:
    return {"failed_tasks": [], "failed_rooms": [], "waiting": [], "unread_rooms": [], "queued_tasks": 0}


async def agent_alerts(
    db: AsyncSession,
    *,
    room_ids: frozenset[str],
    user_id: str,
    inbox_items: list[dict[str, Any]],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    """Return ``agent_id -> alerts`` for agents with at least one alert."""
    if not room_ids:
        return {}
    out: dict[str, dict[str, Any]] = {}

    def entry(agent_id: str) -> dict[str, Any]:
        return out.setdefault(agent_id, _empty())

    assignee = Participant.id == Task.assignee_participant_id
    visible_task = and_(Task.room_id.in_(room_ids), Task.is_silent.is_(False), Participant.agent_id.is_not(None))
    failed = (await db.execute(
        select(Participant.agent_id, Task.id, Task.title, Task.room_id)
        .join(Participant, assignee)
        .where(visible_task, Task.status == "failed")
        .order_by(Task.finished_at.desc(), Task.id)
    )).all()
    for agent_id, task_id, title, room_id in failed:
        entry(agent_id)["failed_tasks"].append({"id": task_id, "title": title, "room_id": room_id})

    queued = (await db.execute(
        select(Participant.agent_id, func.count(Task.id))
        .join(Participant, assignee)
        .where(visible_task, Task.status == "todo")
        .group_by(Participant.agent_id)
    )).all()
    for agent_id, n in queued:
        entry(agent_id)["queued_tasks"] = int(n)

    # The latest completed-or-failed turn per (agent, room) decides.
    latest = func.row_number().over(
        partition_by=(AgentTurn.agent_id, AgentTurn.room_id),
        order_by=(AgentTurn.created_at.desc(), AgentTurn.request_id.desc()),
    ).label("rn")
    finished = (
        select(AgentTurn.agent_id, AgentTurn.room_id, AgentTurn.state, latest)
        .where(AgentTurn.room_id.in_(room_ids), AgentTurn.agent_id.is_not(None),
               AgentTurn.state.in_(("completed", "failed")))
        .subquery()
    )
    for agent_id, room_id in (await db.execute(
        select(finished.c.agent_id, finished.c.room_id)
        .where(finished.c.rn == 1, finished.c.state == "failed")
        .order_by(finished.c.room_id)
    )).all():
        entry(agent_id)["failed_rooms"].append(room_id)

    actions = (await db.execute(
        select(WorkspaceActionRequest.agent_id, WorkspaceActionRequest.id,
               WorkspaceActionRequest.summary, WorkspaceActionRequest.room_id)
        .where(WorkspaceActionRequest.room_id.in_(room_ids), WorkspaceActionRequest.status == "pending",
               WorkspaceActionRequest.expires_at > now)
        .order_by(WorkspaceActionRequest.created_at, WorkspaceActionRequest.id)
    )).all()
    for agent_id, action_id, summary, room_id in actions:
        entry(agent_id)["waiting"].append(
            {"kind": "workspace_action", "id": action_id, "title": summary, "room_id": room_id, "href": None})
    for item in inbox_items:
        kind = item.get("type")
        if kind not in ("approval", "question") or not item.get("needs_action"):
            continue
        agent_id = (item.get(kind) or {}).get("requester_agent_id")
        if agent_id:
            entry(agent_id)["waiting"].append({
                "kind": kind, "id": item["id"], "title": item.get("task_title") or "",
                "room_id": item.get("task_room_id"), "href": item.get("task_href"),
            })

    me = (
        select(Participant.room_id, Participant.last_read_message_seq)
        .where(Participant.user_id == user_id, Participant.room_id.in_(room_ids))
        .subquery()
    )
    for agent_id, room_id in (await db.execute(
        select(AgentTurn.agent_id, Message.room_id)
        .join(Message, Message.id == AgentTurn.accepted_message_id)
        .join(me, me.c.room_id == Message.room_id)
        .where(AgentTurn.state == "completed", AgentTurn.agent_id.is_not(None),
               func.coalesce(AgentTurn.terminal_reason, "") != "agent_skipped",
               Message.seq > func.coalesce(me.c.last_read_message_seq, -1))
        .distinct()
        .order_by(Message.room_id)
    )).all():
        entry(agent_id)["unread_rooms"].append(room_id)

    for alerts in out.values():
        for key in ("failed_tasks", "waiting"):
            alerts[f"{key}_count"] = len(alerts[key])
            alerts[key] = alerts[key][:LIST_LIMIT]
    return out
