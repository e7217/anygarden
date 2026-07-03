"""Add agents.unavailable_code / unavailable_detail / unavailable_since.

Revision ID: 051
Revises: 050
Create Date: 2026-07-03

Rationale
---------
Issue #516 — structured "why can't this agent respond" for the not-running
family (engine change, no machine for engine, spawn failure, crash, engine
drift, no room). Today the no_machine placement failure is swallowed into a
reasonless ``pending`` and the user just sees silence.

- ``unavailable_code`` (String(64), indexed) — machine-readable reason; NULL
  means the agent is fine. The index backs the admin "which agents are
  unavailable" query.
- ``unavailable_detail`` (JSON) — engine name / stderr_tail / exit_code /
  running-vs-db engine. The human message is derived at render time, not
  stored (see ``anygarden.agent_availability``).
- ``unavailable_since`` (DateTime tz) — how long it has been stuck.

All three are nullable with no server default so existing rows land as NULL
(== fine) with no backfill, mirroring 049 (turn_timeout_sec) / 038
(permission_level). Rollback is symmetric.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "051"
down_revision: str = "050"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("unavailable_code", sa.String(64), nullable=True))
        batch.add_column(sa.Column("unavailable_detail", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("unavailable_since", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_index("ix_agents_unavailable_code", ["unavailable_code"])


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_index("ix_agents_unavailable_code")
        batch.drop_column("unavailable_since")
        batch.drop_column("unavailable_detail")
        batch.drop_column("unavailable_code")
