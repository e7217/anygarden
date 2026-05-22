"""Make rooms.project_id nullable and detach existing DMs.

Revision ID: 025
Revises: 024
Create Date: 2026-04-19

Rationale
---------
Issue #179 — the agent DM auto-create path (``api/v1/agents.py``) pins every
DM room to the "first" project, and ``rooms.project_id`` is ``NOT NULL + ON
DELETE CASCADE``. Deleting that arbitrary host project wipes out every
agent's DM alongside it, which the admin never asked for and cannot see in
the delete confirmation dialog.

Fix: make ``project_id`` nullable so DM rooms can live without a project,
then backfill any existing DM rows to ``NULL``. Regular rooms still need a
project — that requirement is enforced at the API layer (``RoomCreate``).

Downgrade deletes orphan DMs because the old schema cannot represent them.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "025"
down_revision: str = "024"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # batch_alter_table needed for SQLite compatibility — SQLite cannot
    # ALTER COLUMN directly; batch mode rebuilds the table. The existing
    # FK constraint name is preserved implicitly because we do not rename
    # the column itself.
    with op.batch_alter_table("rooms") as batch:
        batch.alter_column(
            "project_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )

    # Backfill: any DM room currently carrying a ``project_id`` was placed
    # there by the old ``first_project`` heuristic in agents.py. Detach
    # them so a future project delete leaves them alone. Regular rooms
    # (``is_dm=0``) keep their project_id — they genuinely belong to a
    # project.
    op.execute(
        "UPDATE rooms SET project_id = NULL WHERE is_dm = 1 AND project_id IS NOT NULL"
    )


def downgrade() -> None:
    # The old schema cannot store DM rows with NULL project_id. We
    # cannot silently remap them to some arbitrary project (that was the
    # original bug), so the safest choice is to drop orphan DM rows.
    # Their participants/messages cascade along with the row.
    op.execute("DELETE FROM rooms WHERE is_dm = 1 AND project_id IS NULL")

    with op.batch_alter_table("rooms") as batch:
        batch.alter_column(
            "project_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
