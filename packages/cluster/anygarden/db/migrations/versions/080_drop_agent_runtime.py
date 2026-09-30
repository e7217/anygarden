"""Drop the unused agent runtime selector (issue #746).

``agents.runtime`` (added in 016, #73) chose between the Python
``anygarden-agent`` and the TypeScript ``anygarden-agent-ts`` process. Engine
execution has been Python-only since #679 and the TypeScript package is
removed, so every agent runs ``anygarden-agent`` and the column is dropped.

No agent ever used the TypeScript runtime. Any non-``python`` row is still
logged before the drop so a wrong assumption leaves a trace.
"""

import logging

import sqlalchemy as sa
from alembic import op

revision = "080_drop_agent_runtime"
down_revision = "079_drop_room_wake_triggers"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    legacy = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM agents WHERE runtime != 'python'")
    ).scalar()
    if legacy:
        logger.warning(
            "Dropping agents.runtime: %d agent(s) were not on the python "
            "runtime and will now start with anygarden-agent",
            legacy,
        )
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("runtime")


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column(
                "runtime",
                sa.String(20),
                nullable=False,
                server_default="python",
            )
        )
