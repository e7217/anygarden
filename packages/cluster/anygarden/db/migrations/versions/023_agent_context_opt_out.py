"""Add agents.context_window_opt_out column.

Revision ID: 023
Revises: 022
Create Date: 2026-04-19

Rationale
---------
Issue #148 Part 2 — room-level ambient context sharing (#148 Part 1,
migration 022) is "public space wants a context window", but
operators also need an "I opt out" escape for a specific agent
that is expensive to run (e.g. Gemini) or behaves badly with
extra ingested text. Rather than forcing the choice to the room,
a single nullable-false BOOLEAN on ``agents`` expresses the opt-
out so the agent skips ingest_only broadcasts regardless of the
room setting.

Default FALSE ⇒ no behaviour change on deploy. Part 3 will teach
``decide_policy`` to honour this flag; until then the column is
pure storage that Part 2's API + UI surface.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "023"
down_revision: str = "022"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # SQLite batch path: adding NOT NULL requires ``server_default``
    # so existing rows get backfilled to ``False``.
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column(
                "context_window_opt_out",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("context_window_opt_out")
