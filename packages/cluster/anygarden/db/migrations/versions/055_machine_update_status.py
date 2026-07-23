"""Add machines.update_status / update_error / update_started_at.

Revision ID: 055
Revises: 054
Create Date: 2026-07-23

Rationale
---------
Issue #550 — server-driven machine self-update. The admin triggers an
update on a machine; the server records progress so the UI can show
updating → success/failed:

- ``update_status`` (String(16), nullable) — "updating" | "success" |
  "failed"; NULL until an update is triggered.
- ``update_error`` (String(512), nullable) — failure detail reported by
  the daemon (or a timeout note); NULL on success.
- ``update_started_at`` (DateTime, nullable) — when the update was
  triggered, used to time out a stuck "updating".

All three are nullable with no server default, so existing rows land as
NULL and need no backfill. Uses ``batch_alter_table`` for SQLite
(runtime DB). Rollback is symmetric.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "055"
down_revision: str = "054"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("machines") as batch:
        batch.add_column(sa.Column("update_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("update_error", sa.String(512), nullable=True))
        batch.add_column(sa.Column("update_started_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("machines") as batch:
        batch.drop_column("update_started_at")
        batch.drop_column("update_error")
        batch.drop_column("update_status")
