"""Rename llm_gateway_usage to the engine-neutral usage_ledger (#655, task #92).

The usage stream is engine-agnostic and outlives any one gateway. This
migration renames the physical table and its indices so the storage layer no
longer names a single engine. Rows, columns and data are fully preserved —
SQLite supports ``ALTER TABLE ... RENAME``, and index names are recreated
under the new convention because SQLite does not rename index names directly.

Revision ID: 074_usage_ledger
Revises: 073_agent_provider
Create Date: 2026-09-22
"""

from __future__ import annotations

from alembic import op

revision = "074_usage_ledger"
down_revision = "073_agent_provider"
branch_labels = None
depends_on = None

_OLD_TABLE = "llm_gateway_usage"
_NEW_TABLE = "usage_ledger"

_INDEX_RENAMES = [
    ("ix_llm_gateway_usage_timestamp", "ix_usage_ledger_timestamp"),
    ("ix_llm_gateway_usage_agent_ts", "ix_usage_ledger_agent_ts"),
    ("ix_llm_gateway_usage_model_ts", "ix_usage_ledger_model_ts"),
]


def upgrade() -> None:
    with op.batch_alter_table(_OLD_TABLE) as batch:
        for old_name, new_name in _INDEX_RENAMES:
            batch.drop_index(old_name)
    # SQLite's batch mode recreates the table under the new name; rows are
    # copied verbatim by the batch machinery, preserving all history.
    op.rename_table(_OLD_TABLE, _NEW_TABLE)
    with op.batch_alter_table(_NEW_TABLE) as batch:
        batch.create_index(
            "ix_usage_ledger_timestamp", ["timestamp"], unique=False
        )
        batch.create_index(
            "ix_usage_ledger_agent_ts", ["agent_id", "timestamp"], unique=False
        )
        batch.create_index(
            "ix_usage_ledger_model_ts", ["model_name", "timestamp"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table(_NEW_TABLE) as batch:
        for old_name, new_name in _INDEX_RENAMES:
            batch.drop_index(new_name)
    op.rename_table(_NEW_TABLE, _OLD_TABLE)
    with op.batch_alter_table(_OLD_TABLE) as batch:
        batch.create_index(
            "ix_llm_gateway_usage_timestamp", ["timestamp"], unique=False
        )
        batch.create_index(
            "ix_llm_gateway_usage_agent_ts", ["agent_id", "timestamp"], unique=False
        )
        batch.create_index(
            "ix_llm_gateway_usage_model_ts", ["model_name", "timestamp"], unique=False
        )
