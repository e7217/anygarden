"""Add rooms.speaker_strategy and orchestrator columns.

Revision ID: 024
Revises: 023
Create Date: 2026-04-19

Rationale
---------
Issue #159 Phase A — unlock room-scoped speaker strategies
(``mentioned_only`` default, ``round_robin``, ``orchestrator``) and
the orchestrator-driven next-speaker pointer. Schema is introduced
up-front so Phase B/C/D can ship independently on top.

Four nullable columns are added with safe defaults so existing rooms
are unaffected:

- ``speaker_strategy`` — the active strategy. ``NOT NULL`` with
  ``DEFAULT 'mentioned_only'`` preserves current behaviour.
- ``orchestrator_agent_id`` — the agent that drives handoffs in the
  ``orchestrator`` strategy. Nullable FK to ``agents.id``;
  deliberately separate from ``representative_agent_id`` (cross-room
  query path) so role assignments stay legible.
- ``next_speaker_participant_id`` — the participant the orchestrator
  handed off to. Nullable FK to ``participants.id``. Read by
  ``decide_policy``'s ``orchestrator`` branch.
- ``current_speaker_index`` — round-robin cursor. NOT NULL default 0.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "024"
down_revision: str = "023"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ``batch_alter_table`` requires named FK constraints — without
    # ``name=`` Alembic raises "Constraint must have a name" when the
    # batch helper flushes (older SQLAlchemy batch implementation).
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


def downgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.drop_column("current_speaker_index")
        batch.drop_column("next_speaker_participant_id")
        batch.drop_column("orchestrator_agent_id")
        batch.drop_column("speaker_strategy")
