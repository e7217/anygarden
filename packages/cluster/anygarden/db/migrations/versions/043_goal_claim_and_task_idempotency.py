"""Goal CAS claim + Task idempotency key (exactly-once goal firing).

Revision ID: 043
Revises: 042
Create Date: 2026-06-18

Issue #449 (Wave 1b) — the goal scheduler previously fired by
SELECT-then-trigger with no CAS and no dedup, so a restart / second
replica could replay the same slot N times, and a Run-now on a
scheduled goal advanced ``next_run_at`` (a latent stampede + a bug).

This migration adds the two columns the new contract needs:

1. ``tasks.idempotency_key`` (nullable String(128)) + a UNIQUE index
   ``uq_tasks_idempotency_key``. The scheduler CAS claim and the
   Run-now path both stamp a deterministic key per slot; the UNIQUE
   index turns a duplicate fire into an IntegrityError (caught and
   collapsed into an idempotent response) instead of a second Task.

2. ``agent_goals.claimed_at`` (nullable datetime) — records when the
   scheduler last won the atomic claim. Observability only.

No backfill — a nullable UNIQUE allows multiple NULLs on both SQLite
and Postgres, so pre-existing goal Tasks stay NULL and the index
builds cleanly. Dedup is forward-only: only fires after this
migration carry a key. ``ix_tasks_goal_created`` is left intact (the
in-flight dedup probe reuses it).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "043"
down_revision: str = "042"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── tasks.idempotency_key (+ UNIQUE index) ─────────────────────
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column("idempotency_key", sa.String(length=128), nullable=True)
        )
    op.create_index(
        "uq_tasks_idempotency_key",
        "tasks",
        ["idempotency_key"],
        unique=True,
    )

    # ── agent_goals.claimed_at ─────────────────────────────────────
    with op.batch_alter_table("agent_goals") as batch:
        batch.add_column(
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_goals") as batch:
        batch.drop_column("claimed_at")

    op.drop_index("uq_tasks_idempotency_key", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("idempotency_key")
