"""Migrate agents off removed engine values.

Revision ID: 035
Revises: 034
Create Date: 2026-04-28

Rationale
---------
Issue #292 — the four engine adapters ``openai``, ``anthropic``,
``openhands``, ``deep-agents`` are removed in this PR because they
were never wired into ``RoomHandlerSupervisor`` (no timeout/cycle
guards) and never received the per-engine context plumbing
(``<room_conversation>``, roster, shared-context). Selecting any of
them silently produced a degraded session.

If a deployment happens to have agent rows still pinned to one of
those values, this migration redirects them to ``claude-code`` so
the next spawn boots a working adapter instead of raising
``Unknown engine`` in the agent registry. ``claude-code`` is the
default fallback because the production fleet's existing agents
already lean on that path; switching to it is the closest behaviour
preserving choice we have without prompting an admin.

The migration is intentionally a no-op for fresh installs: a
``WHERE engine IN (...)`` filter touches zero rows when the values
were never used. Running ``alembic upgrade`` on such a DB is safe.

The downgrade is a no-op — restoring the removed engine values would
also require restoring the deleted adapter modules, which the
downgrade path can't do. We trade reversibility for not silently
re-introducing broken engine selections.
"""

from __future__ import annotations

from alembic import op

revision: str = "035"
down_revision: str = "034"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


_DEAD_ENGINES = ("openai", "anthropic", "openhands", "deep-agents")


def upgrade() -> None:
    placeholders = ", ".join(f"'{name}'" for name in _DEAD_ENGINES)
    op.execute(
        f"UPDATE agents SET engine = 'claude-code' "
        f"WHERE engine IN ({placeholders})"
    )
    op.execute(
        f"DELETE FROM machine_engines WHERE engine IN ({placeholders})"
    )


def downgrade() -> None:
    pass
