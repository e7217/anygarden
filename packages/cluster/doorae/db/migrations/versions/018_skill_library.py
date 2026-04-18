"""Add skill_library and agent_skills tables.

Revision ID: 018
Revises: 017
Create Date: 2026-04-18

Rationale
---------
Issue #119 (Phase 1 — SKILL.md-only MVP).

Prior to this migration, sharing a Claude / Codex / Gemini-CLI skill
across agents meant the admin pasted SKILL.md into each agent's
manifest files individually. With N agents that's N copies and a
linear sync burden every time upstream updates the skill.

This migration introduces two tables so a skill becomes a first-class
library entry:

- ``skill_library``: one row per (source, name, pinned_rev) tuple.
  ``source`` is the GitHub repo (``vercel-labs/agent-skills``),
  ``name`` is the directory under ``skills/`` inside that repo,
  ``pinned_rev`` is the resolved commit SHA at registration time.
  The SKILL.md body is stored in ``skill_md`` and hashed into
  ``content_hash`` (sha256) so drift relative to upstream can be
  detected later. ``extra_files`` and ``scripts_detected`` are
  reserved for Phase 3 and stay as empty JSON objects in Phase 1.
- ``agent_skills``: many-to-many link. A skill can attach to many
  agents, and one agent can compose multiple skills. Deleting either
  side cascades the link row.

``approved_by`` is nullable and unused in Phase 1 (Phase 2 will wire
the approval gate). Keeping the column now avoids a second migration
when that phase lands.

Both tables use the project's standard String(36) UUID PK convention
(matches AgentFile, Agent, Machine, etc.) so row ids are portable
across engines and don't require Postgres ``uuid`` support.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: str = "017"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "skill_library",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("pinned_rev", sa.String(64), nullable=False),
        sa.Column("skill_md", sa.Text(), nullable=False),
        # ``extra_files`` holds ``{relative_path: body}`` once Phase 3
        # lands (the whole directory passthrough). Defaults to ``{}``
        # in Phase 1 so the column stays useful without a later
        # migration to change nullability.
        sa.Column("extra_files", sa.JSON(), nullable=False),
        # ``scripts_detected`` records the non-SKILL.md paths the
        # GitHub tree returned at registration time — purely UI
        # metadata in Phase 1 ("this skill ships 3 scripts we didn't
        # materialize yet"). Phase 3 replaces this with the actual
        # extra_files body.
        sa.Column("scripts_detected", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(36), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source", "name", "pinned_rev",
            name="uq_skill_library_source_name_rev",
        ),
    )
    op.create_index(
        "ix_skill_library_source_name",
        "skill_library",
        ["source", "name"],
    )

    op.create_table(
        "agent_skills",
        sa.Column(
            "agent_id",
            sa.String(36),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "skill_library_id",
            sa.String(36),
            sa.ForeignKey("skill_library.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "attached_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_agent_skills_skill",
        "agent_skills",
        ["skill_library_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_skills_skill", table_name="agent_skills")
    op.drop_table("agent_skills")
    op.drop_index("ix_skill_library_source_name", table_name="skill_library")
    op.drop_table("skill_library")
