"""Room messages as an agent reads them (#866).

Shared by the ``read_room_messages`` MCP tool (the agent pulls history) and
the conversation context the server attaches to a delivered turn (the server
pushes what the agent missed). Both show one *conversation* at a time — the
room's main channel or one thread — because that is how an engine session is
scoped (``room_execution._context_scope``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Message

# Long enough for a typical chat message, short enough that a page of them
# stays a few KB. A cut message says so (``truncated``) so the model never
# quotes half a message as the whole of it.
MAX_CONTENT_CHARS = 1000

# Server bookkeeping the UI hides (frontend ``lib/systemMessages.ts``); an
# agent reading the room should see what people see.
HIDDEN_SYSTEM_ORIGINS = frozenset(
    {"auto_route_request", "auto_route_response", "peer_ask_results"}
)


def in_conversation(stmt: Select, thread_root_id: str | None) -> Select:
    """Narrow *stmt* to the main channel (roots) or one thread (root + replies)."""
    if thread_root_id is None:
        return stmt.where(Message.root_message_id.is_(None))
    return stmt.where(
        or_(Message.root_message_id == thread_root_id, Message.id == thread_root_id)
    )


def is_visible(message: Message) -> bool:
    origin = (message.extra_metadata or {}).get("system_origin")
    return origin not in HIDDEN_SYSTEM_ORIGINS


async def describe(
    db: AsyncSession,
    room_id: str,
    rows: Iterable[Message],
    *,
    self_pids: Iterable[str] = (),
    with_thread_counts: bool = False,
) -> list[dict[str, Any]]:
    """Render *rows* (ascending) as speaker-labelled dicts, hidden ones dropped."""
    from anygarden.rooms.roster import build_participants_brief

    visible = [m for m in rows if is_visible(m)]
    if not visible:
        return []
    briefs = {b.id: b for b in await build_participants_brief(db, room_id=room_id)}
    mine = set(self_pids)
    replies: dict[str, int] = {}
    if with_thread_counts:
        roots = [m.id for m in visible if m.root_message_id is None]
        if roots:
            replies = dict(
                (
                    await db.execute(
                        select(Message.root_message_id, func.count())
                        .where(Message.root_message_id.in_(roots))
                        .group_by(Message.root_message_id)
                    )
                ).all()
            )
    out: list[dict[str, Any]] = []
    for m in visible:
        brief = briefs.get(m.participant_id or "")
        content = m.content or ""
        truncated = len(content) > MAX_CONTENT_CHARS
        if truncated:
            content = content[:MAX_CONTENT_CHARS] + "…"
        if (m.extra_metadata or {}).get("deleted_at"):
            content, truncated = "(deleted)", False
        entry: dict[str, Any] = {
            "seq": m.seq,
            "message_id": m.id,
            "speaker": "you" if m.participant_id in mine else (
                brief.display_name if brief is not None else "unknown"
            ),
            "kind": brief.kind if brief is not None else "unknown",
            "content": content,
            "truncated": truncated,
        }
        if m.root_message_id is not None:
            entry["thread_root_id"] = m.root_message_id
        if replies.get(m.id):
            entry["thread_replies"] = replies[m.id]
        out.append(entry)
    return out


def render_lines(messages: list[dict[str, Any]]) -> str:
    """One line per message: ``[seq 12] dev(agent): ...``."""
    lines = []
    for m in messages:
        thread = (
            f" [thread: {m['thread_replies']} replies, thread_root_id={m['message_id']}]"
            if m.get("thread_replies") else ""
        )
        lines.append(f"[seq {m['seq']}] {m['speaker']}({m['kind']}): {m['content']}{thread}")
    return "\n".join(lines)
