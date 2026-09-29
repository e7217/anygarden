"""Drop the unused per-room wake trigger policy (issue #740).

``rooms.wake_triggers`` (added in 069, D-1 #624) had no settings UI and no
production caller: ``mention`` was never checked, ``reminder`` was only read
by an emitter nothing invoked, and ``message`` only stamped REST sends. The
column and the ``metadata.wake_trigger`` stamp are removed; the message
reactions table from the same migration stays.
"""

import sqlalchemy as sa
from alembic import op

revision = "079_drop_room_wake_triggers"
down_revision = "078_machine_engine_auth_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.drop_column("wake_triggers")


def downgrade() -> None:
    with op.batch_alter_table("rooms") as batch:
        batch.add_column(
            sa.Column(
                "wake_triggers",
                sa.JSON(),
                nullable=False,
                server_default='["mention", "reminder"]',
            )
        )
