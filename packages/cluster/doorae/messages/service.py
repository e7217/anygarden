"""Message service — append and paginate, integrating with the repository."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Message, Participant, User
from doorae.db.repository import append_message as _repo_append, replay_since_seq

if TYPE_CHECKING:
    from doorae.db.models import Room, Task
    from doorae.ws.manager import ConnectionManager


async def append_message(
    db: AsyncSession,
    room_id: str,
    participant_id: str,
    content: str,
    metadata: dict | None = None,
) -> Message:
    """Persist a new message and return it with the assigned seq."""
    return await _repo_append(db, room_id, participant_id, content, metadata)


async def get_message_history(
    db: AsyncSession,
    room_id: str,
    since_seq: int = 0,
    limit: int = 50,
) -> list[Message]:
    """Return paginated messages for a room, ordered by seq ascending.

    If *since_seq* is 0, returns the latest *limit* messages.
    Otherwise returns messages with seq > since_seq.
    """
    if since_seq > 0:
        return await replay_since_seq(db, room_id, since_seq, limit)

    # Return last `limit` messages (most recent first, then reverse)
    result = await db.execute(
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(Message.seq.desc())
        .limit(limit)
    )
    messages = list(result.scalars().all())
    messages.reverse()
    return messages


# ── Task assignment injection (#266) ─────────────────────────────────


async def inject_task_assignment_message(
    db: AsyncSession,
    *,
    room: "Room",
    task: "Task",
    sender_participant_id: str | None,
    event: Literal["assigned", "reassigned"] = "assigned",
) -> Message:
    """Drop a synthetic mention-bearing message into *room* announcing
    that *task* has been (re)assigned to its current assignee.

    The agent that owns the assignee participant wakes up through its
    existing ``decide_policy`` mention path — see plan §3.2 decision 1.
    The frontend renders these as compact task cards via the
    ``metadata.task_assignment`` flag (plan §3.1, Step 8).

    ``sender_participant_id`` is the participant the message is recorded
    against. Caller is responsible for picking it: prefer the room's
    orchestrator participant, else the inviting user's participant. If
    neither is available the caller may pass ``None`` — the row is
    persisted with a NULL ``participant_id`` and stamped with
    ``metadata.system_origin = "task_assignment"`` so renderers can
    distinguish it from a stray no-participant message.
    """
    assignee_pid = task.assignee_participant_id
    if not assignee_pid:
        raise ValueError(
            "inject_task_assignment_message requires task.assignee_participant_id"
        )

    metadata: dict = {
        "mentions": [{"type": "user", "id": assignee_pid}],
        "task_assignment": {
            "task_id": task.id,
            "assignee_pid": assignee_pid,
            "event": event,
        },
    }
    if sender_participant_id is None:
        metadata["system_origin"] = "task_assignment"

    content = f"<@user:{assignee_pid}> [TASK] {task.title}"

    return await _repo_append(
        db,
        room.id,
        sender_participant_id,
        content,
        metadata,
    )


async def _build_task_ws_payload(
    db: AsyncSession, task: "Task", room_name: str
) -> dict[str, Any]:
    """Shape the WS ``task.updated`` ``task`` payload.

    The frontend keys its 2차 view (에이전트 프로필) by ``agent_id``,
    so we resolve the assignee's underlying agent here once. Bot+human
    rooms are mixed: an assignee may be a user participant, in which
    case ``agent_id`` is ``None`` and the 2차 view simply ignores the
    row.
    """
    agent_id: str | None = None
    if task.assignee_participant_id:
        p = await db.get(Participant, task.assignee_participant_id)
        if p is not None:
            agent_id = p.agent_id
    return {
        "id": task.id,
        "room_id": task.room_id,
        "room_name": room_name,
        "title": task.title,
        "status": task.status,
        "assignee_participant_id": task.assignee_participant_id,
        "agent_id": agent_id,
        "created_by": task.created_by,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


async def _admin_user_ids(db: AsyncSession) -> set[str]:
    """Return the set of admin user ids — recipients of the agent
    profile 2차 view fanout. Recomputed per call: the cohort is small
    in practice and admin flag changes are rare."""
    rows = (
        await db.execute(select(User.id).where(User.is_admin.is_(True)))
    ).scalars().all()
    return set(rows)


async def fanout_task_event(
    db: AsyncSession,
    *,
    manager: "ConnectionManager | None",
    event: Literal["created", "updated", "deleted", "assigned", "reassigned"],
    task: "Task",
    room_name: str,
) -> None:
    """Push a ``task.updated`` frame to both the room channel (1차) and
    every admin user's WS sessions (2차) — see plan §3.1 / §3.2 결정 4.

    No-op when *manager* is ``None`` (tests that don't wire up a
    ``ConnectionManager``). Failures inside the manager are already
    swallowed at the per-recipient level.
    """
    if manager is None:
        return
    from doorae.ws.protocol import TaskUpdateOut

    payload = await _build_task_ws_payload(db, task, room_name)
    frame = TaskUpdateOut(event=event, task=payload)
    await manager.broadcast(task.room_id, frame)
    user_ids = await _admin_user_ids(db)
    if user_ids:
        await manager.push_to_users(user_ids, frame)
