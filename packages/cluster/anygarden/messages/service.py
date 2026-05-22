"""Message service — append and paginate, integrating with the repository."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Message, Participant, User
from anygarden.db.repository import append_message as _repo_append, replay_since_seq

if TYPE_CHECKING:
    from anygarden.db.models import Room, Task
    from anygarden.ws.manager import ConnectionManager


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
    manager: "ConnectionManager | None" = None,
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

    ``manager`` — when provided, the persisted row is also broadcast as
    a ``MessageOut`` frame on the room's WS channel, mirroring the
    ``fanout_task_event`` pattern. Without this fanout the agent never
    receives the wake-up frame even though the row sits in the DB
    (#314). ``None`` is accepted for tests / unit isolation.
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

    # Multi-line content (#275, #338). First line stays the canonical
    # ``<@user:pid> [TASK] {title}`` form so ``decide_policy`` mention
    # matching, frontend title extraction, and message-log readers keep
    # working unchanged. The following action block is a self-instruction
    # the assignee LLM reads on this very turn: it tells the agent to
    # report progress through the existing ``mark_task_status`` MCP tool
    # so status updates flow without admins toggling them by hand.
    #
    # The frontend short-circuits on ``metadata.task_assignment`` and
    # renders only ``TaskAssignmentCard``, so this trailing prose does
    # not leak into the chat UI; ``stripTaskMentionPrefix`` likewise
    # keeps the card title to the first line. See plan §3.1 / §6 R3.
    content = (
        f"<@user:{assignee_pid}> [TASK] {task.title}\n"
        "\n"
        "**REQUIRED ACTIONS** - 이 task는 당신에게 배정되었습니다. "
        "다음 두 단계를 반드시 수행하세요:\n"
        "\n"
        "1. **시작 직후**: "
        f'`mark_task_status(task_id="{task.id}", status="in_progress")` '
        "를 호출하여 작업 착수를 기록.\n"
        "2. **응답 완료 시**: 같은 도구로 `status=\"done\"` 을 호출. "
        "차단되면 `status=\"blocked\"` + 이유 설명.\n"
        "\n"
        "이 호출을 누락하면 작업이 5분 후 자동 실패 처리됩니다."
    )

    msg = await _repo_append(
        db,
        room.id,
        sender_participant_id,
        content,
        metadata,
    )

    # #314 — fanout the persisted row as a ``MessageOut`` frame so the
    # agent's WS session receives the wake-up signal. Without this the
    # row sits silently in the DB until the agent reconnects and
    # ``replay_since_seq`` catches up — for an always-on agent that's
    # effectively never. Mirrors the api/v1/tasks.py path where the
    # router broadcasts after commit; here we accept that scheduler-
    # injected frames can race the commit, but the receiver's frame
    # already carries the full message content (no DB lookup needed)
    # and any reconnect will reconcile via ``replay_since_seq``.
    if manager is not None:
        from anygarden.ws.protocol import MessageOut

        frame = MessageOut(
            id=msg.id,
            room_id=msg.room_id,
            participant_id=msg.participant_id,
            content=msg.content,
            seq=msg.seq,
            created_at=msg.created_at,
            metadata=msg.extra_metadata,
        )
        await manager.broadcast(room.id, frame)

    return msg


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
    from anygarden.ws.protocol import TaskUpdateOut

    payload = await _build_task_ws_payload(db, task, room_name)
    frame = TaskUpdateOut(event=event, task=payload)
    await manager.broadcast(task.room_id, frame)
    user_ids = await _admin_user_ids(db)
    if user_ids:
        await manager.push_to_users(user_ids, frame)
