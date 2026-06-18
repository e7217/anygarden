"""Token budget policies + (room_id, timestamp) usage index.

Revision ID: 044
Revises: 043
Create Date: 2026-06-18

Issue #453 (reliability Wave 1d) — adds the policy table that backs the
token-cost ledger + invocation-block gate, plus an index the ledger's
per-room SUM relies on.

1. ``token_budget_policies`` — one ceiling per scope (global / agent /
   room). ``hard_stop_enabled`` defaults False so no policy enforces
   anything until an admin enables it: merging this migration cannot
   change runtime behaviour. ``scope_id`` carries no FK (a deleted
   agent/room must not silently drop the operator's policy). Indexed on
   (scope_type, scope_id, is_active) — the exact predicate the ledger
   filters active policies by.

2. ``ix_llm_gateway_usage_room_ts`` on ``llm_gateway_usage (room_id,
   timestamp)`` — the room-scoped window SUM mirrors the existing
   (agent_id, timestamp) index the per-agent SUM already uses. 026
   created the table without it.

Downgrade reverses both in reverse order.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "044"
down_revision: str = "043"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "token_budget_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=True),
        sa.Column("token_ceiling", sa.Integer(), nullable=False),
        sa.Column(
            "warn_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("80"),
        ),
        sa.Column(
            "window_kind",
            sa.String(24),
            nullable=False,
            server_default="rolling_24h",
        ),
        sa.Column(
            "hard_stop_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_token_budget_policies_scope",
        "token_budget_policies",
        ["scope_type", "scope_id", "is_active"],
    )

    # Mirror the existing (agent_id, timestamp) index so the room-scoped
    # window SUM the ledger issues is just as cheap. 026 created the
    # usage table with timestamp / (agent_id, ts) / (model_name, ts)
    # only.
    op.create_index(
        "ix_llm_gateway_usage_room_ts",
        "llm_gateway_usage",
        ["room_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_gateway_usage_room_ts", table_name="llm_gateway_usage"
    )
    op.drop_index(
        "ix_token_budget_policies_scope", table_name="token_budget_policies"
    )
    op.drop_table("token_budget_policies")
