"""Add saved_messages, activity_logs, tasks tables and FTS5 index.

Revision ID: 008
Revises: 007
Create Date: 2026-04-13

Features:
- saved_messages: user bookmarked messages
- activity_logs: agent lifecycle event trail
- tasks: per-room task board
- messages_fts: full-text search on message content
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"


def upgrade() -> None:
    op.create_table(
        "saved_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "message_id", name="uq_saved_user_message"),
    )

    op.create_table(
        "activity_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("details", sa.JSON, nullable=True),
    )
    op.create_index("ix_activity_logs_agent_ts", "activity_logs", ["agent_id", "timestamp"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("room_id", sa.String(36), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), server_default="todo"),
        sa.Column("assignee_participant_id", sa.String(36), sa.ForeignKey("participants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tasks_room_status", "tasks", ["room_id", "status"])

    # FTS5 virtual table for message full-text search.
    # SQLite FTS5 requires an INTEGER rowid. Since messages.id is a
    # UUID string, we use a separate content table with a rowid alias.
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            room_id UNINDEXED,
            participant_id UNINDEXED,
            message_id UNINDEXED,
            created_at UNINDEXED
        )
    """)

    # Triggers to keep FTS in sync with the messages table.
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(content, room_id, participant_id, message_id, created_at)
            VALUES (NEW.content, NEW.room_id, NEW.participant_id, NEW.id, NEW.created_at);
        END
    """)
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
            DELETE FROM messages_fts WHERE message_id = OLD.id;
        END
    """)
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE OF content ON messages BEGIN
            DELETE FROM messages_fts WHERE message_id = OLD.id;
            INSERT INTO messages_fts(content, room_id, participant_id, message_id, created_at)
            VALUES (NEW.content, NEW.room_id, NEW.participant_id, NEW.id, NEW.created_at);
        END
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS messages_fts_update")
    op.execute("DROP TRIGGER IF EXISTS messages_fts_delete")
    op.execute("DROP TRIGGER IF EXISTS messages_fts_insert")
    op.execute("DROP TABLE IF EXISTS messages_fts")
    op.drop_table("tasks")
    op.drop_table("activity_logs")
    op.drop_table("saved_messages")
