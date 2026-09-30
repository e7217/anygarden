"""Async fan-in for ``ask_peer`` questions (#762).

``ask_peer`` used to post a caller's questions only after its final reply,
so the caller never saw the answers and nothing woke it again. The flow is
now:

1. ``ask_peer`` opens (or joins) a :class:`PeerAskGroup` for the caller's
   turn, posts each question in the trigger's thread at once, and starts
   the peer's turn (:func:`post_question`).
2. The caller's reply for that turn is not posted; it is kept as the
   group's draft (:func:`absorb_caller_reply`) and the turn closes.
3. Every terminal turn transition calls :func:`on_turn_terminal`, which
   settles the matching target row or marks the caller closed. When both
   sides are done the group becomes ``ready``.
4. The turn-recovery worker (:func:`dispatch_peer_ask_groups`) expires
   overdue targets, wakes the caller of every ``ready`` group with a hidden
   result message in the caller's original conversation, and keeps the
   caller's "waiting for peers" indicator alive meanwhile.

The state lives in the database because peer work often takes minutes and
must survive a restart. Only :func:`dispatch_peer_ask_groups` broadcasts;
everything else runs inside the caller's transaction.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    AgentTurn,
    Message,
    Participant,
    PeerAskGroup,
    PeerAskTarget,
)

logger = structlog.get_logger(__name__)

DEFAULT_DEADLINE_SECONDS = 1800
WAITING_HEARTBEAT_SECONDS = 3.0
MAX_ORIGIN_CHARS = 1000
RESULTS_SYSTEM_ORIGIN = "peer_ask_results"
TERMINAL_TARGET_STATES = frozenset({"completed", "failed", "cancelled", "timeout"})

# group_id → monotonic time of the last "waiting" typing frame.
_last_heartbeat: dict[str, float] = {}


def deadline_seconds() -> int:
    raw = os.environ.get("ANYGARDEN_PEER_ASK_DEADLINE_SEC")
    try:
        value = int(raw) if raw else DEFAULT_DEADLINE_SECONDS
    except ValueError:
        value = DEFAULT_DEADLINE_SECONDS
    return max(1, value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def group_for_caller(db: AsyncSession, request_id: str) -> PeerAskGroup | None:
    return (
        await db.execute(
            select(PeerAskGroup).where(PeerAskGroup.caller_request_id == request_id)
        )
    ).scalar_one_or_none()


async def target_for_turn(db: AsyncSession, request_id: str) -> PeerAskTarget | None:
    return (
        await db.execute(
            select(PeerAskTarget).where(PeerAskTarget.request_id == request_id)
        )
    ).scalar_one_or_none()


async def _targets(db: AsyncSession, group_id: str) -> list[PeerAskTarget]:
    return list(
        (
            await db.execute(
                select(PeerAskTarget)
                .where(PeerAskTarget.group_id == group_id)
                .order_by(PeerAskTarget.created_at, PeerAskTarget.id)
            )
        ).scalars()
    )


async def open_or_join_group(
    db: AsyncSession, *, caller_turn: AgentTurn, caller_participant_id: str
) -> PeerAskGroup:
    """Return the caller turn's group, creating it on the first ask."""
    group = await group_for_caller(db, caller_turn.request_id)
    if group is not None:
        return group
    trigger = (
        await db.get(Message, caller_turn.trigger_message_id)
        if caller_turn.trigger_message_id
        else None
    )
    group = PeerAskGroup(
        id=str(uuid4()),
        room_id=caller_turn.room_id,
        caller_participant_id=caller_participant_id,
        caller_agent_id=caller_turn.agent_id,
        caller_request_id=caller_turn.request_id,
        trigger_message_id=caller_turn.trigger_message_id,
        scope_thread_root_id=(
            trigger.root_message_id if trigger is not None else caller_turn.thread_root_id
        ),
        state="collecting",
        deadline_at=_now() + timedelta(seconds=deadline_seconds()),
    )
    # Two ask_peer calls from one turn can race to create the group; the
    # loser re-reads the winner's row instead of failing its questions.
    try:
        async with db.begin_nested():
            db.add(group)
            await db.flush()
    except IntegrityError:
        existing = await group_for_caller(db, caller_turn.request_id)
        if existing is None:
            raise
        return existing
    return group


@dataclass
class PostedQuestion:
    target: PeerAskTarget
    message: Message
    turn: AgentTurn


async def post_question(
    db: AsyncSession,
    *,
    group: PeerAskGroup,
    target: Participant,
    question: str,
) -> PostedQuestion:
    """Post one question in the trigger's thread and start the peer's turn.

    The question is authored by the caller and mentions the target, so the
    peer's turn is a hop-2 turn (``turn_hop``) and cannot ask on. The target
    row is flushed before the turn exists so a turn that is born cancelled
    (agent not running) settles it through ``on_turn_terminal``.
    """
    from anygarden.messages.service import append_message
    from anygarden.turns.service import create_turn

    # Anchor on the message the exchange started from: after a wake the
    # trigger is the hidden result message, which cannot host a visible
    # thread.
    origin = await _origin_message(db, group)
    thread_root = (
        (origin.root_message_id or origin.id)
        if origin is not None
        else group.scope_thread_root_id
    )
    msg = await append_message(
        db,
        group.room_id,
        group.caller_participant_id,
        f"<@user:{target.id}> {question}",
        {
            "mentions": [{"type": "user", "id": target.id}],
            "peer_ask": {"via": "tool", "group_id": group.id},
            "peer_depth": 1,
            "kind": "peer_query",
        },
        thread_root_id=thread_root,
    )
    rid = str(uuid4())
    row = PeerAskTarget(
        id=str(uuid4()),
        group_id=group.id,
        target_participant_id=target.id,
        question=question,
        question_message_id=msg.id,
        request_id=rid,
        state="pending",
        forwarded_requests=[],
    )
    db.add(row)
    await db.flush()
    turn = await create_turn(
        db,
        room_id=group.room_id,
        participant_id=target.id,
        agent_id=target.agent_id,
        trigger_message_id=msg.id,
        thread_root_id=msg.root_message_id,
        request_id=rid,
    )
    return PostedQuestion(target=row, message=msg, turn=turn)


async def absorb_caller_reply(db: AsyncSession, *, request_id: str, content: str) -> bool:
    """Keep a caller's reply as its draft instead of posting it.

    Returns ``False`` when the turn asked no peers (post normally).
    """
    group = await group_for_caller(db, request_id)
    if group is None:
        return False
    text = content.strip()
    if text:
        group.caller_draft = (
            f"{group.caller_draft}\n\n{text}" if group.caller_draft else text
        )
    return True


async def record_forwarded_request(
    db: AsyncSession, *, request_id: str, participant_id: str, question: str
) -> bool:
    """Hand a hop-2 peer's own ``ask_peer`` call back to its caller."""
    row = await target_for_turn(db, request_id)
    if row is None:
        return False
    row.forwarded_requests = [
        *(row.forwarded_requests or []),
        {"participant_id": participant_id, "question": question},
    ]
    return True


def _target_outcome(turn: AgentTurn) -> tuple[str, str | None]:
    if turn.state == "completed":
        if turn.terminal_reason == "agent_skipped":
            return "completed", "declined"
        return "completed", None
    return turn.state, turn.terminal_reason


async def on_turn_terminal(db: AsyncSession, turn: AgentTurn) -> None:
    """Settle the fan-in state a terminal turn belongs to (DB only)."""
    row = await target_for_turn(db, turn.request_id)
    if row is not None and row.state not in TERMINAL_TARGET_STATES:
        row.state, row.reason = _target_outcome(turn)
        row.reply_message_id = turn.accepted_message_id
        row.finished_at = _now()
        group = await db.get(PeerAskGroup, row.group_id)
        if group is not None:
            await _mark_ready_if_done(db, group)
    group = await group_for_caller(db, turn.request_id)
    if group is not None and group.caller_closed_at is None:
        group.caller_closed_at = _now()
        await _mark_ready_if_done(db, group)


async def _mark_ready_if_done(db: AsyncSession, group: PeerAskGroup) -> None:
    if group.state != "collecting" or group.caller_closed_at is None:
        return
    targets = await _targets(db, group.id)
    if all(t.state in TERMINAL_TARGET_STATES for t in targets):
        group.state = "ready"


async def _origin_message(db: AsyncSession, group: PeerAskGroup) -> Message | None:
    """The message the whole exchange started from (skips earlier wakes)."""
    msg = await db.get(Message, group.trigger_message_id) if group.trigger_message_id else None
    seen: set[str] = set()
    while msg is not None and msg.id not in seen:
        seen.add(msg.id)
        origin_id = ((msg.extra_metadata or {}).get(RESULTS_SYSTEM_ORIGIN) or {}).get(
            "origin_message_id"
        )
        if not origin_id:
            return msg
        msg = await db.get(Message, origin_id)
    return msg


def render_results(
    *,
    caller_pid: str,
    origin: str | None,
    draft: str | None,
    entries: list[dict[str, Any]],
) -> str:
    """Build the wake message the caller reads to write its final answer."""
    lines = [
        (
            f"<@user:{caller_pid}> [ask_peer results] The peers you asked have "
            "finished. Write your final answer to the user now, in one message, "
            "using these results together with your own findings. Say which "
            "peer failed or timed out and why. Do not ask again for anything "
            "already answered here."
        ),
    ]
    if origin:
        excerpt = origin if len(origin) <= MAX_ORIGIN_CHARS else origin[:MAX_ORIGIN_CHARS] + "…"
        lines += ["", "Original request:", excerpt]
    if draft:
        lines += ["", "Your draft from before you asked (not posted):", draft]
    for i, e in enumerate(entries, 1):
        status = e["state"] + (f" ({e['reason']})" if e.get("reason") else "")
        lines += ["", f"{i}. {e['name']} — {status}", f"Question: {e['question']}"]
        if e.get("answer"):
            lines += ["Answer:", e["answer"]]
        elif e["state"] == "completed":
            lines.append("Answer: (no reply was posted)")
        for fwd in e.get("forwarded") or []:
            lines.append(
                f"It asked you to ask {fwd['name']}: {fwd['question']} "
                "(call ask_peer yourself if you still need this)"
            )
    return "\n".join(lines)


async def _wake(db: AsyncSession, group: PeerAskGroup) -> Message | None:
    """Post the hidden result message and start the caller's next turn."""
    from anygarden.messages.service import append_message
    from anygarden.rooms.roster import build_participants_brief
    from anygarden.turns.service import create_turn

    caller = await db.get(Participant, group.caller_participant_id)
    agent_id = group.caller_agent_id or (caller.agent_id if caller else None)
    if caller is None or agent_id is None:
        group.state = "abandoned"
        logger.warning("peer_ask.abandoned", group_id=group.id, reason="caller_gone")
        return None

    names = {b.id: b.display_name for b in await build_participants_brief(db, room_id=group.room_id)}
    entries: list[dict[str, Any]] = []
    for t in await _targets(db, group.id):
        reply_id = t.reply_message_id
        if reply_id is None and t.state == "completed":
            turn = await db.get(AgentTurn, t.request_id)
            reply_id = turn.accepted_message_id if turn is not None else None
        reply = await db.get(Message, reply_id) if reply_id else None
        entries.append({
            "name": names.get(t.target_participant_id, t.target_participant_id),
            "state": t.state,
            "reason": t.reason,
            "question": t.question,
            "answer": reply.content if reply is not None else None,
            "forwarded": [
                {
                    "name": names.get(f.get("participant_id", ""), f.get("participant_id")),
                    "question": f.get("question", ""),
                }
                for f in t.forwarded_requests or []
            ],
        })
    origin = await _origin_message(db, group)
    content = render_results(
        caller_pid=caller.id,
        origin=origin.content if origin is not None else None,
        draft=group.caller_draft,
        entries=entries,
    )
    msg = await append_message(
        db,
        group.room_id,
        None,
        content,
        {
            "mentions": [{"type": "user", "id": caller.id}],
            "system_origin": RESULTS_SYSTEM_ORIGIN,
            RESULTS_SYSTEM_ORIGIN: {
                "group_id": group.id,
                "origin_message_id": origin.id if origin is not None else None,
            },
        },
        thread_root_id=group.scope_thread_root_id,
    )
    rid = str(uuid4())
    turn = await create_turn(
        db,
        room_id=group.room_id,
        participant_id=caller.id,
        agent_id=agent_id,
        trigger_message_id=msg.id,
        thread_root_id=msg.root_message_id,
        request_id=rid,
    )
    group.wake_request_id = rid
    group.woken_at = _now()
    group.state = "woken" if turn.state == "pending" else "abandoned"
    if group.state == "abandoned":
        logger.warning(
            "peer_ask.abandoned", group_id=group.id, reason=turn.terminal_reason
        )
    return msg


async def _advance_collecting(db: AsyncSession, now: datetime) -> None:
    """Time out overdue targets and re-check readiness of waiting groups.

    The terminal hook decides readiness from its own transaction, so a peer
    and the caller closing at the same moment can each miss the other's
    write; re-checking every closed-caller group here repairs that.
    """
    groups = (
        await db.execute(
            select(PeerAskGroup).where(
                PeerAskGroup.state == "collecting",
                (PeerAskGroup.deadline_at <= now)
                | PeerAskGroup.caller_closed_at.isnot(None),
            )
        )
    ).scalars().all()
    for group in groups:
        if _aware(group.deadline_at) <= now:
            for t in await _targets(db, group.id):
                if t.state not in TERMINAL_TARGET_STATES:
                    t.state = "timeout"
                    t.reason = "deadline"
                    t.finished_at = now
        await _mark_ready_if_done(db, group)


async def _heartbeat(db: AsyncSession, manager: Any) -> None:
    from anygarden.ws.protocol import TypingOut

    waiting = (
        await db.execute(
            select(PeerAskGroup).where(
                PeerAskGroup.state == "collecting",
                PeerAskGroup.caller_closed_at.isnot(None),
            )
        )
    ).scalars().all()
    live = {g.id for g in waiting}
    for gid in [g for g in _last_heartbeat if g not in live]:
        _last_heartbeat.pop(gid, None)
    if not waiting:
        return
    now = time.monotonic()
    names: dict[str, dict[str, str]] = {}
    for group in waiting:
        if now - _last_heartbeat.get(group.id, 0.0) < WAITING_HEARTBEAT_SECONDS:
            continue
        _last_heartbeat[group.id] = now
        if group.room_id not in names:
            from anygarden.rooms.roster import build_participants_brief

            names[group.room_id] = {
                b.id: b.display_name
                for b in await build_participants_brief(db, room_id=group.room_id)
            }
        targets = await _targets(db, group.id)
        await manager.broadcast(
            group.room_id,
            TypingOut(
                room_id=group.room_id,
                participant_id=group.caller_participant_id,
                is_typing=True,
                stage="waiting_peers",
                waiting_done=sum(t.state in TERMINAL_TARGET_STATES for t in targets),
                waiting_total=len(targets),
                waiting_names=[
                    names[group.room_id].get(t.target_participant_id, "")
                    for t in targets
                ],
            ),
        )


async def dispatch_peer_ask_groups(session_factory: Any, manager: Any | None) -> int:
    """Expire, wake and keep alive fan-in groups; return how many woke."""
    now = _now()
    async with session_factory() as db:
        await _advance_collecting(db, now)
        await db.commit()
        ready_ids = (
            await db.execute(select(PeerAskGroup.id).where(PeerAskGroup.state == "ready"))
        ).scalars().all()

    woken = 0
    for gid in ready_ids:
        try:
            woken += await _wake_one(session_factory, manager, gid)
        # One bad group must not stall the others or the rest of the tick.
        except Exception:
            logger.exception("peer_ask.wake_failed", group_id=gid)

    if manager is not None:
        async with session_factory() as db:
            await _heartbeat(db, manager)
    return woken


async def _wake_one(session_factory: Any, manager: Any | None, gid: str) -> int:
    """Claim and wake one ready group; return 1 when the caller was woken."""
    from anygarden.messages.serialization import message_to_frame
    from anygarden.ws.protocol import TypingOut

    async with session_factory() as db:
        # Claim the group so two workers never wake a caller twice; the
        # claim rolls back with the wake if anything fails before commit.
        claimed = await db.execute(
            update(PeerAskGroup)
            .where(PeerAskGroup.id == gid, PeerAskGroup.state == "ready")
            .values(state="waking")
        )
        if claimed.rowcount != 1:
            return 0
        group = await db.get(PeerAskGroup, gid)
        if group is None:
            return 0
        msg = await _wake(db, group)
        await db.commit()
        room_id = group.room_id
        caller_pid = group.caller_participant_id
        durable = group.state == "woken"
    _last_heartbeat.pop(gid, None)
    if manager is not None:
        await manager.broadcast(
            room_id,
            TypingOut(room_id=room_id, participant_id=caller_pid, is_typing=False),
        )
        if msg is not None:
            # The caller receives the message through its durable turn;
            # everyone else gets the (UI-hidden) row for history parity.
            frame = message_to_frame(msg)
            await manager.broadcast_tailored(
                room_id,
                lambda pid: None if durable and pid == caller_pid else frame,
            )
    return 1 if durable else 0
