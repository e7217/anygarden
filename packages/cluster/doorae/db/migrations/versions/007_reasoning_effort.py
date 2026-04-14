"""Add agents.reasoning_effort column.

Revision ID: 007
Revises: 006
Create Date: 2026-04-13

Rationale
---------
Allow per-agent reasoning effort configuration (low/medium/high).
Each engine adapter maps this to the appropriate CLI flag:
- Codex CLI: --reasoning-effort
- Claude Code: model selection
- Gemini CLI: --thinking-budget
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"


def upgrade() -> None:
    op.add_column("agents", sa.Column("reasoning_effort", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "reasoning_effort")
