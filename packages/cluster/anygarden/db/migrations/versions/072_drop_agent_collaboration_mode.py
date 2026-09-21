"""Drop agents.collaboration_mode.

Added in migration 034 (#279) as a per-agent ``solo`` | ``collaborative``
switch. It read as a safety control but never was one: the peer-mention
safety net in ``ws/handler.py`` (MAX_PEER_DEPTH plus the per-turn
handoff budget) never consulted the column, so a ``solo`` agent that
emitted a routing token still woke its peer. The column's only real
effect was deciding whether the agent SDK appended the roster and its
peer-mention usage hint to the LLM system prompt — and even that was
applied inconsistently across engines (openhands ignored it entirely).

#644 makes the roster unconditional, which leaves the column with
nothing to express: every agent now knows its room's participants, and
peer traffic stays capped by the budget that was doing the work all
along.

Downgrade restores the column with its original ``'solo'`` default. The
per-row solo/collaborative distinction is NOT recoverable — nor is it
meaningful after this migration, since the behaviour it selected no
longer varies.

Revision ID: 072_drop_agent_collaboration_mode
Revises: 071_interaction_resolutions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "072_drop_agent_collaboration_mode"
down_revision = "071_interaction_resolutions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("collaboration_mode")


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column(
                "collaboration_mode",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'solo'"),
            )
        )
