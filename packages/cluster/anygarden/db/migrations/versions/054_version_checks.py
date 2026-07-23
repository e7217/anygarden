"""Add version_checks table for cached PyPI update checks.

Revision ID: 054
Revises: 053
Create Date: 2026-07-23

Rationale
---------
Issue #546 — the admin "check for updates" action queries PyPI for each
package's latest version and compares it against the running version. The
result is cached in this table so the update badge / admin panel can read
the last-known state without any outbound call, and so it survives a
server restart. A future background poller can fill the same table with
no schema change.

One row per package (``anygarden``, ``anygarden-machine``); ``package`` is
the primary key. ``latest_version`` / ``checked_at`` / ``error`` are all
nullable — a package that has never been checked has no row, and a failed
check records ``error`` with ``latest_version`` left as the prior value or
NULL. Uses ``batch_alter_table``-free ``create_table`` (a fresh table needs
no SQLite batch workaround). Rollback drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "054"
down_revision: str = "053"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "version_checks",
        sa.Column("package", sa.String(64), primary_key=True),
        sa.Column("latest_version", sa.String(64), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("version_checks")
