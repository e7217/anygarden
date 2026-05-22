"""Add MCP server template catalog and per-agent instance tables.

Revision ID: 019
Revises: 018
Create Date: 2026-04-19

Rationale
---------
Issue #124.

Before this migration, wiring an agent to an external MCP server
(github / slack / notion / a company-internal server) meant editing
``.claude/settings.json`` or ``.codex/config.toml`` via the AgentFile
surface, once per (agent, server) pair. With N agents that's N copies
of the same command/args/env skeleton and a linear sync burden every
time the upstream server's config changes.

This migration introduces the two-tier template/instance model the
plan settled on:

- ``mcp_server_templates``: one row per MCP server *definition*. A
  ``name`` (unique) plus the engine-specific config blocks
  (``config_per_engine`` is ``{engine_id: <engine-native config dict>}``)
  plus the list of required env var names. ``source`` distinguishes
  builtin (shipped by cluster, re-seeded at startup) from custom
  (admin-authored via the API). The ``created_by`` column is only
  meaningful for custom rows — NULL on builtin rows.
- ``mcp_server_instances``: many-to-many link between a template and
  an agent, with the *encrypted* per-instance env values. Fernet
  ciphertext is opaque binary, so we store it in a ``LargeBinary``
  column. The UNIQUE ``(template_id, agent_id)`` constraint enforces
  "one instance per template per agent" — re-attaching overwrites
  rather than creating a duplicate.

Both tables use the project's standard ``String(36)`` UUID PK
convention (matches AgentFile, SkillLibraryEntry, etc.) so row ids
stay portable across SQLite and Postgres without needing native UUID
types.

JSON columns (``config_per_engine``, ``required_env_vars``,
``supported_engines``) default to empty dict/list at the ORM layer so
NOT NULL + NULL-from-DB can't collide during the builtin seed path.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "019"
down_revision: str = "018"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_server_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(512), nullable=True),
        # ``config_per_engine`` shape: {engine_id: engine-native config}.
        # For claude-code / gemini-cli that's a JSON-serialisable dict
        # matching the ``mcpServers.<name>`` body the CLI expects. For
        # codex it's the same dict; merge.py renders it into the
        # ``[mcp_servers.<name>]`` TOML section at spawn time.
        sa.Column("config_per_engine", sa.JSON(), nullable=False),
        # Names only — values are stored per instance (encrypted).
        sa.Column("required_env_vars", sa.JSON(), nullable=False),
        # Redundant with ``config_per_engine.keys()`` but stored
        # explicitly so the API can filter templates for an agent's
        # engine without parsing the whole config blob.
        sa.Column("supported_engines", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_mcp_server_templates_name"),
    )
    op.create_index(
        "ix_mcp_server_templates_source",
        "mcp_server_templates",
        ["source"],
    )

    op.create_table(
        "mcp_server_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "template_id",
            sa.String(36),
            sa.ForeignKey("mcp_server_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Fernet token bytes. Callers decrypt via
        # ``MCPSecrets.decrypt_dict`` at render time — the DB only ever
        # sees ciphertext. NULL means "no env values required" (e.g.
        # the filesystem builtin which takes paths only via its config
        # body, not credentials).
        sa.Column("env_values_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "template_id", "agent_id",
            name="uq_mcp_server_instances_template_agent",
        ),
    )
    op.create_index(
        "ix_mcp_server_instances_agent",
        "mcp_server_instances",
        ["agent_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_server_instances_agent",
        table_name="mcp_server_instances",
    )
    op.drop_table("mcp_server_instances")
    op.drop_index(
        "ix_mcp_server_templates_source",
        table_name="mcp_server_templates",
    )
    op.drop_table("mcp_server_templates")
