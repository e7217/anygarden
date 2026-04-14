"""REST endpoint for full-text message search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity
from doorae.dependencies import get_current_identity, get_db

router = APIRouter(prefix="/api/v1/search", tags=["search"])


class SearchResult(BaseModel):
    message_id: str
    room_id: str
    participant_id: str | None = None
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
    """Full-text search across messages using FTS5."""
    # FTS5 query — use highlight() for snippets.
    # If project_id is given, join through rooms to filter.
    if project_id:
        sql = text("""
            SELECT
                fts.message_id,
                fts.room_id,
                fts.participant_id,
                fts.content,
                fts.created_at,
                highlight(messages_fts, 0, '<mark>', '</mark>') as snippet
            FROM messages_fts fts
            JOIN rooms r ON r.id = fts.room_id
            WHERE messages_fts MATCH :query
              AND r.project_id = :project_id
            ORDER BY rank
            LIMIT :limit
        """)
        rows = (await db.execute(sql, {"query": q, "project_id": project_id, "limit": limit})).all()
    else:
        sql = text("""
            SELECT
                message_id,
                room_id,
                participant_id,
                content,
                created_at,
                highlight(messages_fts, 0, '<mark>', '</mark>') as snippet
            FROM messages_fts
            WHERE messages_fts MATCH :query
            ORDER BY rank
            LIMIT :limit
        """)
        rows = (await db.execute(sql, {"query": q, "limit": limit})).all()

    return [
        SearchResult(
            message_id=row.message_id,
            room_id=row.room_id,
            participant_id=row.participant_id,
            content=row.content,
            created_at=str(row.created_at) if row.created_at else "",
            snippet=row.snippet,
        )
        for row in rows
    ]
