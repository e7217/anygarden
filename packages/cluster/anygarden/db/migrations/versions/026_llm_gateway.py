"""Add LLM gateway tables: models, secrets, usage.

Revision ID: 026
Revises: 025
Create Date: 2026-04-20

Rationale
---------
Issue #197. Adds the persistence layer for the embedded LiteLLM
gateway — see ``docs/design/12-llm-gateway.md`` and ADR-004.

Three tables:

- ``llm_gateway_models`` — one row per entry in the rendered
  ``model_list``. ``api_key_ref`` points at a secret row by natural
  key, not by FK, so deleting a secret doesn't cascade into model
  rows (admin may want to re-register the key under the same name).
- ``llm_gateway_secrets`` — encrypted API keys. ``env_var_name`` is
  the PK (it's what other rows reference and what the config writer
  embeds as ``os.environ/ANYGARDEN_LITELLM_<env_var_name>``). Ciphertext
  uses the existing ``ANYGARDEN_MCP_SECRETS_KEY`` Fernet.
- ``llm_gateway_usage`` — one row per relayed request. Indexed on
  timestamp + (agent_id, timestamp) + (model_name, timestamp) to
  cover the admin UI's "by agent" and "by model" aggregations cheaply.
  30-day TTL cron prunes stale rows.

All three tables use ``String(36)`` UUID PKs (except the secret's
natural-key PK) for SQLite/Postgres portability — matches the rest
of the schema.

Downgrade drops in reverse order.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "026"
down_revision: str = "025"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "llm_gateway_models",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("upstream_model", sa.String(255), nullable=False),
        sa.Column("api_key_ref", sa.String(64), nullable=False),
        sa.Column("extra_params", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("model_name", name="uq_llm_gateway_models_name"),
    )
    op.create_index(
        "ix_llm_gateway_models_provider",
        "llm_gateway_models",
        ["provider"],
    )

    op.create_table(
        "llm_gateway_secrets",
        sa.Column("env_var_name", sa.String(64), primary_key=True),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "llm_gateway_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "room_id",
            sa.String(36),
            sa.ForeignKey("rooms.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("identity_kind", sa.String(16), nullable=False),
        sa.Column("identity_id", sa.String(36), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(512), nullable=True),
    )
    op.create_index(
        "ix_llm_gateway_usage_timestamp",
        "llm_gateway_usage",
        ["timestamp"],
    )
    op.create_index(
        "ix_llm_gateway_usage_agent_ts",
        "llm_gateway_usage",
        ["agent_id", "timestamp"],
    )
    op.create_index(
        "ix_llm_gateway_usage_model_ts",
        "llm_gateway_usage",
        ["model_name", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_gateway_usage_model_ts", table_name="llm_gateway_usage")
    op.drop_index("ix_llm_gateway_usage_agent_ts", table_name="llm_gateway_usage")
    op.drop_index("ix_llm_gateway_usage_timestamp", table_name="llm_gateway_usage")
    op.drop_table("llm_gateway_usage")
    op.drop_table("llm_gateway_secrets")
    op.drop_index("ix_llm_gateway_models_provider", table_name="llm_gateway_models")
    op.drop_table("llm_gateway_models")
