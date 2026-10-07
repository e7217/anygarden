"""Drop the room speaker-strategy columns.

Added in migration 024 (#159) for the ``round_robin`` and ``orchestrator``
speaker strategies. #739 made every room mention-only, and #802 removes
the strategies: the server no longer rotates speakers, parses
``[HANDOFF]`` messages or nominates a fallback speaker, so the strategy,
the orchestrator pointer and both cursor columns have nothing left to
express. The project operating lead is the room's
``representative_agent_id``.

Downgrade restores the columns with their original defaults. The
per-room strategy and orchestrator choice are NOT recoverable.

Revision ID: 091_drop_room_speaker_strategy
Revises: 090_native_invocation_accounting
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "091_drop_room_speaker_strategy"
down_revision = "090_native_invocation_accounting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.drop_column("current_speaker_index")
        batch.drop_column("next_speaker_participant_id")
        batch.drop_column("orchestrator_agent_id")
        batch.drop_column("speaker_strategy")


def downgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.add_column(
            sa.Column(
                "speaker_strategy",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'mentioned_only'"),
            )
        )
        batch.add_column(
            sa.Column(
                "orchestrator_agent_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "agents.id",
                    ondelete="SET NULL",
                    name="fk_rooms_orchestrator_agent_id",
                ),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "next_speaker_participant_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "participants.id",
                    ondelete="SET NULL",
                    name="fk_rooms_next_speaker_participant_id",
                ),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "current_speaker_index",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
