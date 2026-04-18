"""Add approve workflow (approved_at) and immutable audit log for skills.

Revision ID: 020
Revises: 019
Create Date: 2026-04-19

Rationale
---------
Issue #125 (Phase 2 — approve gate + audit log).

Phase 1 (#119) left ``skill_library.approved_by`` nullable and
unused — every registered skill was immediately spawnable. That was
fine for a single-admin internal rollout, but multi-admin setups and
compliance auditing need two things:

1. An **explicit approval act**, recorded by *which admin* approved
   the skill and *when*. The ``approved_by`` column already covers
   the "who" (populated from ``Identity.id`` of the approver); this
   migration adds the ``approved_at`` companion so the approval
   timestamp survives even if the admin user row is later deleted
   (the FK is ``SET NULL`` on delete, but ``approved_at`` stays).
2. An **immutable audit trail** that records every state change
   (register / approve / reject / delete / update / attach / detach /
   grandfathered). The new ``skill_library_audits`` table is
   INSERT-ONLY — UPDATE / DELETE paths do not exist in the service
   layer — so the log survives even when the underlying skill row is
   deleted (``skill_library_id`` FK uses ``ondelete=SET NULL`` so the
   log entry stays; ``actor_user_id`` likewise ``SET NULL`` so a
   later user purge can't retroactively rewrite history).

Grandfather path
----------------
On upgrade, every existing ``skill_library`` row (registered in
Phase 1 without the approve concept) is auto-stamped with
``approved_by=<first admin user id>`` and ``approved_at=<now>``, and
a corresponding ``action="grandfathered"`` audit entry is inserted.
This matches plan decision D1: Phase 1 admins already performed an
implicit approval by calling the register endpoint, so forcing them
to re-approve every pre-existing skill would be gratuitous
UX friction during the Phase 2 deploy. The audit entry preserves
traceability — you can always tell a grandfathered row from a
hand-approved one.

The "first admin" heuristic (``ORDER BY created_at ASC LIMIT 1``) is
a reasonable default: on any doorae deploy the system bootstrap
creates the first admin before any skill is registered, so this user
always exists. If the install has *no* admin users at all (pure dev
bootstrap before any signup), the data migration is a no-op — no
skills could have been registered anyway because the register
endpoint is admin-gated.

Audit detail column
-------------------
``detail`` is a free-form JSON blob so the service layer can evolve
the schema without migrations. Current usage (see service.py):

- ``register``: ``{source, name, pinned_rev, after_hash, body_changed}``
- ``approve`` / ``reject``: ``{before_hash, after_hash}``  (hash is the
  canonical-tree hash from Phase 3 — #127)
- ``delete``: ``{source, name, pinned_rev, before_hash}``
- ``attach`` / ``detach``: ``{agent_id}``
- ``grandfathered``: ``{reason: "phase2_migration"}``

Index choice
------------
``(skill_library_id, at DESC)`` covers the audit drawer query ("give
me this skill's recent history"). Global "who did what" queries are
rare enough to not warrant a second index.
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "020"
down_revision: str = "019"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Companion timestamp for ``approved_by`` so the approval
    #    moment survives an admin user purge (FK is SET NULL).
    op.add_column(
        "skill_library",
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # 2. Immutable audit log. FKs are SET NULL (not CASCADE) so the
    #    log entry survives even after the referenced skill/user is
    #    deleted — that's the whole point of an audit trail.
    op.create_table(
        "skill_library_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "skill_library_id",
            sa.String(36),
            sa.ForeignKey("skill_library.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_skill_library_audits_skill_at",
        "skill_library_audits",
        ["skill_library_id", "at"],
    )

    # 3. Grandfather existing Phase 1 rows.
    bind = op.get_bind()
    meta = sa.MetaData()
    skill_library = sa.Table("skill_library", meta, autoload_with=bind)
    users = sa.Table("users", meta, autoload_with=bind)
    audits = sa.Table("skill_library_audits", meta, autoload_with=bind)

    first_admin = bind.execute(
        sa.select(users.c.id)
        .where(users.c.is_admin == sa.true())
        .order_by(users.c.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()

    if first_admin is None:
        # No admin users yet → nothing could have been registered
        # (register is admin-gated). Safe no-op.
        return

    now = datetime.now(timezone.utc)
    pending_rows = bind.execute(
        sa.select(skill_library.c.id).where(skill_library.c.approved_by.is_(None))
    ).scalars().all()

    if not pending_rows:
        return

    # One UPDATE per row is fine; in practice N is small (low tens).
    import uuid as _uuid

    for sid in pending_rows:
        bind.execute(
            skill_library.update()
            .where(skill_library.c.id == sid)
            .values(approved_by=first_admin, approved_at=now)
        )
        bind.execute(
            audits.insert().values(
                id=str(_uuid.uuid4()),
                skill_library_id=sid,
                actor_user_id=first_admin,
                action="grandfathered",
                detail={"reason": "phase2_migration"},
                at=now,
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_skill_library_audits_skill_at",
        table_name="skill_library_audits",
    )
    op.drop_table("skill_library_audits")
    op.drop_column("skill_library", "approved_at")
