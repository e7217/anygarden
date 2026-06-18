"""llm_gateway_usage.cost_usd column.

Revision ID: 047
Revises: 046
Create Date: 2026-06-19

Issue #461 (reliability Wave 2d) — adds a nullable ``cost_usd`` column to
``llm_gateway_usage`` so the gateway-free CLI-engine telemetry path can
record a per-request USD cost alongside the token counts.

CLI engines (claude-code / codex / gemini) bypass the LLM gateway, so
their usage now arrives via the ``engine_call_finished`` LifecycleFrame
and the WS handler writes one ``LLMGatewayUsage`` row per turn. claude-code
self-reports a cost (its SDK's ``total_cost_usd`` — an *estimate*, not a
provider invoice) which lands here; gateway-routed openhands and the
codex/gemini CLIs report no cost and leave the column NULL. The admin
usage aggregation sums it nullable-safe.

The column is nullable with no backfill — existing rows (all
gateway-routed, no cost signal) stay NULL, so merging this migration
cannot change observed aggregates on its own.

Downgrade drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "047"
down_revision: str = "046"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "llm_gateway_usage",
        sa.Column("cost_usd", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_gateway_usage", "cost_usd")
