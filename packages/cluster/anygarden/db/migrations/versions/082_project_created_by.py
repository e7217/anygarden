"""Record who created each project so access can follow it (issue #783).

Existing projects keep ``created_by`` NULL: their creator is unknown, so
only a global admin may delete them, and they stay visible to members
of their rooms.
"""

import sqlalchemy as sa
from alembic import op

revision = "082_project_created_by"
down_revision = "081_peer_ask_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite cannot add a foreign key with plain ALTER TABLE; batch mode
    # rebuilds the table. The explicit FK name is required by batch mode.
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column(
                "created_by",
                sa.String(36),
                sa.ForeignKey(
                    "users.id",
                    ondelete="SET NULL",
                    name="fk_projects_created_by",
                ),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("created_by")
