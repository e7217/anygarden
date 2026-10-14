"""Record which attempt fences actually spent a transient retry (EXE-10).

The transient retry budget used to be derived from attempts left
``interrupted`` with reason ``MODEL_TRANSIENT_FAILURE``. A fence that
recovery then closed without a retry (gate revoked, admission denied,
budget exhausted) was counted too, so a later manual retry of the same
request saw one transient retry too many and one lease retry too few.
``transient_retry`` is set only on the fenced attempt whose recovery
actually scheduled the transient retry, and nothing changes it afterwards.

Backfill: an old transient fence counts when the turn has a later attempt
(the best evidence available that the fence led to a retry).

Revision ID: 093_turn_attempt_transient_retry
Revises: 092_task_input_requests
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "093_turn_attempt_transient_retry"
down_revision = "092_task_input_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_turn_attempts") as batch:
        batch.add_column(
            sa.Column(
                "transient_retry",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    op.execute(
        """
        UPDATE agent_turn_attempts SET transient_retry = 1
        WHERE state = 'interrupted' AND reason = 'MODEL_TRANSIENT_FAILURE'
          AND EXISTS (
            SELECT 1 FROM agent_turn_attempts AS later
            WHERE later.turn_id = agent_turn_attempts.turn_id
              AND later.attempt_number > agent_turn_attempts.attempt_number
          )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("agent_turn_attempts") as batch:
        batch.drop_column("transient_retry")
