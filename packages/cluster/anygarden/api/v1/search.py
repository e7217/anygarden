"""REST endpoint for full-text message search."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import bindparam, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, Participant, Room, User
from anygarden.dependencies import get_current_identity, get_db
from anygarden.rooms.authorization import accessible_room_ids


def _fts_created_at_to_iso(value: object) -> str:
    """Normalize FTS row's ``created_at`` to a UTC-aware ISO string.

    Issue #93 — FTS virtual tables store timestamps as opaque TEXT
    (SQLite's ``YYYY-MM-DD HH:MM:SS[.ffffff]``), bypassing
    ``UtcDateTime``. We parse and re-emit with an explicit ``+00:00``
    so browsers don't interpret the string as local time.
    """
    if not value:
        return ""
    raw = str(value)
    try:
        dt = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

# FTS highlight() markers. Control characters cannot come from chat input
# the way a literal "<mark>" can, so the snippet can be HTML-escaped first
# and only these markers turned back into markup.
_HL_OPEN = "\x02"
_HL_CLOSE = "\x03"
_MENTION_TOKEN = re.compile(r"<@user:([^>]+)>|<#room:([^>]+)>")


def _render_snippet(
    raw: str, users: dict[str, str], rooms: dict[str, str]
) -> str:
    """Turn a marker-highlighted FTS snippet into safe HTML.

    Message text is escaped so user content never becomes markup; mention
    tokens become ``@name`` / ``#room`` so results read like the chat.
    """

    def mention(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return f"@{users.get(match.group(1), '?')}"
        return f"#{rooms.get(match.group(2), '?')}"

    # Markers can land inside a token when the query hits the token text;
    # strip them there so the token still resolves.
    cleaned = re.sub(
        r"<[@#][^>]*>",
        lambda m: _MENTION_TOKEN.sub(
            mention, m.group(0).replace(_HL_OPEN, "").replace(_HL_CLOSE, "")
        ),
        raw,
    )
    return (
        html.escape(cleaned, quote=False)
        .replace(_HL_OPEN, "<mark>")
        .replace(_HL_CLOSE, "</mark>")
    )


async def _mention_names(
    db: AsyncSession, snippets: list[str], visible_room_ids: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    user_ids: set[str] = set()
    room_ids: set[str] = set()
    for snippet in snippets:
        plain = snippet.replace(_HL_OPEN, "").replace(_HL_CLOSE, "")
        for match in _MENTION_TOKEN.finditer(plain):
            if match.group(1) is not None:
                user_ids.add(match.group(1))
            elif match.group(2) in visible_room_ids:
                # Only name rooms the caller can already see.
                room_ids.add(match.group(2))
    users: dict[str, str] = {}
    if user_ids:
        rows = await db.execute(
            select(Participant.id, Agent.name, User.display_name, User.email)
            .outerjoin(Agent, Agent.id == Participant.agent_id)
            .outerjoin(User, User.id == Participant.user_id)
            .where(Participant.id.in_(user_ids))
        )
        for pid, agent_name, display_name, email in rows:
            name = agent_name or display_name or email
            if name:
                users[pid] = name
    rooms: dict[str, str] = {}
    if room_ids:
        rows = await db.execute(
            select(Room.id, Room.name).where(Room.id.in_(room_ids))
        )
        rooms = {rid: name for rid, name in rows}
    return users, rooms


router = APIRouter(prefix="/api/v1/search", tags=["search"])


class SearchResult(BaseModel):
    message_id: str
    room_id: str
    participant_id: str | None = None
    parent_message_id: str | None = None
    root_message_id: str | None = None
    seq: int
    content: str
    created_at: str
    snippet: str


@router.get("", response_model=list[SearchResult])
async def search_messages(
    q: str = Query(..., min_length=1, description="Search query"),
    project_id: str | None = Query(None, description="Filter by project"),
    limit: int = Query(20, ge=1, le=100),
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    """Full-text search across messages using FTS5.

    If the ``messages_fts`` index is absent (e.g. a deployment whose DB
    predates the index, or a Postgres backend where FTS5 is unavailable),
    the underlying ``OperationalError`` is mapped to a 503 so a missing
    index degrades gracefully instead of leaking a 500 (#473).
    """
    allowed_room_ids = await accessible_room_ids(
        db,
        identity=identity,
        scope="search.messages",
    )

    # FTS5 query — use highlight() for snippets.
    # If project_id is given, join through rooms to filter.
    if project_id:
        sql = text("""
            SELECT
                messages_fts.message_id,
                messages_fts.room_id,
                messages_fts.participant_id,
                messages_fts.content,
                messages_fts.created_at,
                m.parent_message_id,
                m.root_message_id,
                m.seq,
                highlight(messages_fts, 0, char(2), char(3)) as snippet
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.message_id
            JOIN rooms r ON r.id = messages_fts.room_id
            WHERE messages_fts MATCH :query
              AND messages_fts.room_id IN :room_ids
              AND r.project_id = :project_id
            ORDER BY rank
            LIMIT :limit
        """)
        params = {
            "query": q,
            "room_ids": list(allowed_room_ids),
            "project_id": project_id,
            "limit": limit,
        }
    else:
        sql = text("""
            SELECT
                messages_fts.message_id,
                messages_fts.room_id,
                messages_fts.participant_id,
                messages_fts.content,
                messages_fts.created_at,
                m.parent_message_id,
                m.root_message_id,
                m.seq,
                highlight(messages_fts, 0, char(2), char(3)) as snippet
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.message_id
            WHERE messages_fts MATCH :query
              AND messages_fts.room_id IN :room_ids
            ORDER BY rank
            LIMIT :limit
        """)
        params = {
            "query": q,
            "room_ids": list(allowed_room_ids),
            "limit": limit,
        }

    sql = sql.bindparams(bindparam("room_ids", expanding=True))

    try:
        rows = (await db.execute(sql, params)).all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="Search index unavailable"
        ) from exc

    users, rooms = await _mention_names(
        db, [row.snippet for row in rows], set(allowed_room_ids)
    )
    return [
        SearchResult(
            message_id=row.message_id,
            room_id=row.room_id,
            participant_id=row.participant_id,
            parent_message_id=row.parent_message_id,
            root_message_id=row.root_message_id,
            seq=row.seq,
            content=row.content,
            created_at=_fts_created_at_to_iso(row.created_at),
            snippet=_render_snippet(row.snippet, users, rooms),
        )
        for row in rows
    ]
