"""Add agents.description column.

Revision ID: 033
Revises: 032
Create Date: 2026-04-25

Rationale
---------
Issue #271 — surface a short, public-facing self-introduction for each
agent so other agents (in the LLM roster) and humans (in the mention
popover and participant list) can recognize the agent by more than its
name. Distinct from ``agents_md`` which is the agent's *self-directed*
prompt body. Null default: existing rows simply have no description and
fall back to the legacy "name only" rendering paths.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "033"
down_revision: str = "032"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column(
                "description",
                sa.Text(),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("description")
