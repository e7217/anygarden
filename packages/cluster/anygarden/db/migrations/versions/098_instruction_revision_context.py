"""Record why an instruction revision was made (#930).

Campaign retrospectives update an agent's AGENTS.md and skills without a
person approving each change, so the revision keeps its reason:
``context`` holds e.g. ``{campaign_id, attempt_id, rationale}``. ``source``
is an unconstrained ``String(16)``; it now also takes ``"campaign"``.

Revision ID: 098_instruction_revision_context
Revises: 097_campaigns
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "098_instruction_revision_context"
down_revision = "097_campaigns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_instruction_revisions") as batch:
        batch.add_column(sa.Column("context", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_instruction_revisions") as batch:
        batch.drop_column("context")
