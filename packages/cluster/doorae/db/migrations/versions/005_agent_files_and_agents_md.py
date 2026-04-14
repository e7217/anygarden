"""Add agents.agents_md column and agent_files table.

Revision ID: 005
Revises: 004
Create Date: 2026-04-11

Rationale
---------
Phase 0 of the per-agent directory plan. Each agent now has:

- ``agents.agents_md`` — the AGENTS.md body (single source of truth
  for instructions, following the agents.md standard).
- ``agent_files`` rows — the rest of the per-agent directory tree,
  one row per file (``skills/<name>/SKILL.md``, ``.codex/config.toml``,
  ``.gemini/settings.json``, etc.). The machine materializes these on
  spawn under ``~/.doorae/agents/<id>/`` and reconciles the on-disk
  tree against the manifest, so deletes also take effect.

Both additions are nullable / optional so existing agents keep
working via the legacy ``profile_yaml`` flow until they're migrated.

See ``docs/plans/2026-04-11-per-agent-directory-skills.md`` and
``docs/decisions/002-per-agent-directory-with-server-manifest.md``.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: str = "004"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("agents_md", sa.Text(), nullable=True),
    )
    op.create_table(
        "agent_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "agent_id", "path", name="uq_agent_files_agent_path"
        ),
    )
    op.create_index(
        "ix_agent_files_agent", "agent_files", ["agent_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_files_agent", table_name="agent_files")
    op.drop_table("agent_files")
    op.drop_column("agents", "agents_md")
