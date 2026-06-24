"""Migrate agents.engine 'codex' to 'codex-cli'.

Revision ID: 050
Revises: 049
Create Date: 2026-06-24

Rationale
---------
Issue #506 — the SDK-based ``codex`` engine (``integrations/codex.py``) is
removed in favour of the decoupled ``codex-cli`` (exec) engine
(#496/#498/#500, deprecated the SDK in #502). Agents pinned to
``engine='codex'`` would otherwise become unspawnable (no adapter registered),
so this repoints them to ``codex-cli`` — a verified, behaviour-equivalent
replacement (same models, permission tiers, and workspace/CODEX_HOME
isolation, confirmed during #502/#506 E2E).

Downgrade is a deliberate no-op: the SDK codex adapter no longer exists, so
reverting the value to 'codex' would leave the agents unspawnable.
"""

from __future__ import annotations

from alembic import op

revision: str = "050"
down_revision: str = "049"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # #506 — repoint SDK codex agents to the codex-cli (exec) engine.
    op.execute("UPDATE agents SET engine = 'codex-cli' WHERE engine = 'codex'")


def downgrade() -> None:
    # No-op: the SDK codex adapter was removed (#506); restoring
    # engine='codex' would leave migrated agents without an adapter.
    pass
