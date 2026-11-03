"""Where a person cleared an agent's engine session in a room (#986).

``agent_room_memories.session_reset_seq`` is the ``seq`` of the room's
"session cleared" notice. Conversation context handed to the agent's next
turns starts after it; NULL means the session was never cleared.

Revision ID: 102_agent_room_session_reset
Revises: 101_workspace_draft_requests
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "102_agent_room_session_reset"
down_revision = "101_workspace_draft_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_room_memories") as batch:
        batch.add_column(sa.Column("session_reset_seq", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_room_memories") as batch:
        batch.drop_column("session_reset_seq")
