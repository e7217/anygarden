"""Add ``skill_library.created_by_agent_id`` for agent-authored skills.

Revision ID: 021
Revises: 019
Create Date: 2026-04-19

Rationale
---------
Issue #120 — agents can now author their own skills through an MCP
``create_skill`` tool.  An agent-authored skill is keyed to its
creator so:

- ``resolve_for_agent`` can surface the row to its owning agent even
  when ``approved_by IS NULL`` (the #119 approval gate tolerates
  "self-authored, auto-approved" as an orthogonal axis);
- admin UI can filter "agent authored" separately from the canonical
  GitHub-backed library;
- ``promote`` (admin action) flips the column back to NULL, marking
  the entry as shared library.

Pre-existing skill rows (registered through the admin API in #119)
keep ``created_by_agent_id = NULL``, which is the "shared library"
marker going forward.

The column is a plain nullable ``String(36)`` FK to ``agents.id``
with ``ON DELETE SET NULL`` so deleting the authoring agent leaves
the skill in the library for an admin to decide what to do (same
pattern as ``Room.representative_agent_id``).

A plain (non-unique) index supports the "list my skills" query in
the MCP tool path — unique-per-agent names are enforced at the
service layer because the existing
``(source, name, pinned_rev)`` constraint is incompatible with
agent-authored rows (each row has its own synthetic ``source``).

#125 (approval gate) landed first as revision 020; this migration
chains on 020 so the two land cleanly in order without another
rebase.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: str = "020"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # SQLite needs batch mode to add a FK; Postgres tolerates it too.
    # Using batch_alter_table keeps the migration identical across both
    # backends (same trick 017_agent_avatar / 016_agent_runtime use).
    # The FK constraint needs an explicit name — alembic's batch mode
    # refuses to apply an anonymous constraint on SQLite (see upstream
    # ``alembic/operations/batch.py::ApplyBatchImpl.add_constraint``).
    with op.batch_alter_table("skill_library") as batch_op:
        batch_op.add_column(
            sa.Column(
                "created_by_agent_id",
                sa.String(36),
                sa.ForeignKey(
                    "agents.id",
                    ondelete="SET NULL",
                    name="fk_skill_library_created_by_agent",
                ),
                nullable=True,
            )
        )
    op.create_index(
        "ix_skill_library_created_by_agent",
        "skill_library",
        ["created_by_agent_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_skill_library_created_by_agent", table_name="skill_library"
    )
    with op.batch_alter_table("skill_library") as batch_op:
        batch_op.drop_column("created_by_agent_id")
