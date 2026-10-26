"""Remember which evaluator an evaluation ran (#930).

``campaign_attempts.eval_evaluator_digest`` is the sha256 of the
evaluator a cycle's evaluation was sent with. A result adopts its pin
hashes only while that is still the campaign's evaluator, so an edit
during a running evaluation does not re-pin the old files.

Revision ID: 099_campaign_eval_evaluator_digest
Revises: 098_instruction_revision_context
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "099_campaign_eval_evaluator_digest"
down_revision = "098_instruction_revision_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_attempts") as batch:
        batch.add_column(sa.Column("eval_evaluator_digest", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("campaign_attempts") as batch:
        batch.drop_column("eval_evaluator_digest")
