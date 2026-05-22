"""Add rooms.description column.

Revision ID: 006
Revises: 005
Create Date: 2026-04-12

Rationale
---------
Sub-rooms need a description field so that the delegation auto-inline
(``spawner._compose_agents_md``) can inject meaningful context into the
agent's system prompt. The LLM uses the sub-room name + description to
decide whether to delegate a task.

See ``docs/decisions/003-delegation-orchestration-strategy.md``.
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"


def upgrade() -> None:
    op.add_column("rooms", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("rooms", "description")
