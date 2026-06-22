"""Message full-text-search (FTS5) DDL — the living source of truth.

Migration 008 first created the ``messages_fts`` virtual table and its
sync triggers, but migrations are frozen historical snapshots. The
fresh-DB bootstrap path (``app._ensure_schema_ready`` Case 2) does
``create_all + stamp`` and never replays migrations, so without this
helper a brand-new install has no FTS index and every authenticated
search hits ``OperationalError: no such table: messages_fts`` (#473).

The statements below are copied verbatim from migration 008 and all use
``IF NOT EXISTS`` so :func:`create_message_fts` is idempotent and safe to
call on any SQLite connection (boot, tests, future re-use). 008 is left
untouched on purpose: a frozen migration must not import live app code,
or editing this file later would silently change history.

FTS5 is SQLite-only; callers must guard on ``dialect.name == "sqlite"``.
"""

from __future__ import annotations

from sqlalchemy import text

# FTS5 virtual table for message full-text search.
# SQLite FTS5 requires an INTEGER rowid. Since messages.id is a
# UUID string, we use a separate content table with a rowid alias.
_CREATE_FTS_TABLE = """
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        content,
        room_id UNINDEXED,
        participant_id UNINDEXED,
        message_id UNINDEXED,
        created_at UNINDEXED
    )
"""

# Triggers to keep FTS in sync with the messages table.
_CREATE_FTS_INSERT_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(content, room_id, participant_id, message_id, created_at)
        VALUES (NEW.content, NEW.room_id, NEW.participant_id, NEW.id, NEW.created_at);
    END
"""

_CREATE_FTS_DELETE_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
        DELETE FROM messages_fts WHERE message_id = OLD.id;
    END
"""

_CREATE_FTS_UPDATE_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE OF content ON messages BEGIN
        DELETE FROM messages_fts WHERE message_id = OLD.id;
        INSERT INTO messages_fts(content, room_id, participant_id, message_id, created_at)
        VALUES (NEW.content, NEW.room_id, NEW.participant_id, NEW.id, NEW.created_at);
    END
"""

# Ordered: the table must exist before its triggers.
MESSAGE_FTS_STATEMENTS: tuple[str, ...] = (
    _CREATE_FTS_TABLE,
    _CREATE_FTS_INSERT_TRIGGER,
    _CREATE_FTS_DELETE_TRIGGER,
    _CREATE_FTS_UPDATE_TRIGGER,
)


async def create_message_fts(conn) -> None:
    """Create the ``messages_fts`` table and its sync triggers (idempotent).

    ``conn`` is an ``AsyncConnection``. All statements use
    ``IF NOT EXISTS``, so calling this on a connection that already has
    the FTS index is a no-op. FTS5 is SQLite-only — callers are
    responsible for the ``dialect.name == "sqlite"`` guard.
    """
    for statement in MESSAGE_FTS_STATEMENTS:
        await conn.execute(text(statement))
