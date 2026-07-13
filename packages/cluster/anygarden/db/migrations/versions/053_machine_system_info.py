"""Add machines.description / lan_ip / os_platform for daemon system info.

Revision ID: 053
Revises: 052
Create Date: 2026-07-13

Rationale
---------
Issue #523 — surface a machine's detected system info (real hostname, LAN
IP, OS, CPU cores, RAM) in the admin UI, and split the user-facing label
away from the auto-detected hostname.

- ``description`` (String(255), nullable) — user-supplied free-form label /
  note. Replaces the former user-entered ``hostname`` input, which becomes a
  daemon-detected value overwritten on register.
- ``lan_ip`` (String(64), nullable) — primary LAN IPv4 reported by the daemon.
- ``os_platform`` (String(255), nullable) — ``platform.platform()`` string.

``cpu_cores`` / ``memory_gb`` already exist (migration 001) and are left as
is — #523 only starts populating them from the daemon; no schema change.

All three columns are nullable with no server default, so existing rows land
as NULL and need no backfill (mirrors 051). Uses ``batch_alter_table`` for
SQLite compatibility (the runtime DB is SQLite). Rollback is symmetric.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "053"
down_revision: str = "052"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("machines") as batch:
        batch.add_column(sa.Column("description", sa.String(255), nullable=True))
        batch.add_column(sa.Column("lan_ip", sa.String(64), nullable=True))
        batch.add_column(sa.Column("os_platform", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("machines") as batch:
        batch.drop_column("os_platform")
        batch.drop_column("lan_ip")
        batch.drop_column("description")
