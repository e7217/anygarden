"""Tests for #519 — duplicate participants must not brick a room.

A ``(room, user)`` pair used to be able to hold more than one
``participants`` row (no uniqueness guard). ``require_room_member`` then
raised ``MultipleResultsFound``, which 500'd the messages/read REST
endpoints and was swallowed as a false 4003 on the WS handshake.

These tests pin three guarantees:

1. ``require_room_member`` tolerates a legacy duplicate and returns the
   highest-privilege row (admin/owner) instead of raising.
2. The partial UNIQUE index rejects a second row for the same
   ``(room, user)`` / ``(room, agent)``.
3. Migration 052 dedupes pre-existing duplicates (keeping admin) and
   installs the enforcing indexes.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from anygarden.auth.dependencies import Identity, require_room_member
from anygarden.db.models import Participant, Project, Room, User


async def _seed_room(db) -> Room:
    project = Project(name="P")
    db.add(project)
    await db.flush()
    room = Room(project_id=project.id, name="R")
    db.add(room)
    await db.flush()
    return room


class TestRequireRoomMemberToleratesDuplicates:
    @pytest.mark.asyncio
    async def test_returns_admin_row_when_user_has_duplicate_participants(
        self, db
    ) -> None:
        user = User(email="dup@test.com", password_hash="x")
        db.add(user)
        await db.flush()
        room = await _seed_room(db)

        # Simulate a legacy DB that predates migration 052: drop the guard
        # so the duplicate rows can be inserted, then add admin + member.
        await db.execute(text("DROP INDEX IF EXISTS uq_participants_room_user"))
        db.add(Participant(room_id=room.id, user_id=user.id, role="member"))
        db.add(Participant(room_id=room.id, user_id=user.id, role="admin"))
        await db.commit()

        identity = Identity(kind="user", id=user.id)
        participant = await require_room_member(room.id, identity, db)

        assert participant.role == "admin", (
            "duplicate rows must not raise; the admin/owner row wins so the "
            "caller keeps its highest privilege"
        )


class TestUniqueIndexBlocksDuplicates:
    @pytest.mark.asyncio
    async def test_duplicate_user_participant_rejected(self, db) -> None:
        user = User(email="u@test.com", password_hash="x")
        db.add(user)
        await db.flush()
        room = await _seed_room(db)
        db.add(Participant(room_id=room.id, user_id=user.id, role="admin"))
        await db.commit()

        db.add(Participant(room_id=room.id, user_id=user.id, role="member"))
        with pytest.raises(IntegrityError):
            await db.commit()


class TestMigration052:
    @pytest.mark.asyncio
    async def test_dedupes_existing_duplicates_and_enforces_uniqueness(
        self,
    ) -> None:
        from anygarden.app import _alembic_action
        from anygarden.db.engine import build_engine, build_session_factory

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            db_url = f"sqlite+aiosqlite:///{db_path}"
            # Build the schema at 051 — before the uniqueness guard — so
            # duplicate rows can be seeded the way a legacy DB accrued them.
            await _alembic_action("upgrade", db_url, "051")

            engine = build_engine(db_url)
            factory = build_session_factory(engine)
            try:
                async with factory() as db:
                    # Seed through the 051 schema directly. The current ORM
                    # includes columns introduced after 051 (notably the room
                    # authorization fields in 057), so using ORM INSERTs here
                    # would make this historical migration test invalid.
                    now = datetime.now(UTC).isoformat()
                    project_id = str(uuid4())
                    room_id = str(uuid4())
                    user_id = str(uuid4())
                    await db.execute(
                        text(
                            "INSERT INTO projects (id, name, created_at) "
                            "VALUES (:id, 'P', :created_at)"
                        ),
                        {"id": project_id, "created_at": now},
                    )
                    await db.execute(
                        text(
                            "INSERT INTO rooms "
                            "(id, project_id, name, created_at) "
                            "VALUES (:id, :project_id, 'R', :created_at)"
                        ),
                        {
                            "id": room_id,
                            "project_id": project_id,
                            "created_at": now,
                        },
                    )
                    await db.execute(
                        text(
                            "INSERT INTO users "
                            "(id, email, password_hash, created_at) "
                            "VALUES (:id, 'dup@test.com', 'x', :created_at)"
                        ),
                        {"id": user_id, "created_at": now},
                    )
                    # Member row joined *after* the admin row; dedupe must
                    # keep the admin row regardless of join order.
                    await db.execute(
                        text(
                            "INSERT INTO participants "
                            "(id, room_id, user_id, role, joined_at) "
                            "VALUES (:id, :room_id, :user_id, 'admin', :joined_at)"
                        ),
                        {
                            "id": str(uuid4()),
                            "room_id": room_id,
                            "user_id": user_id,
                            "joined_at": now,
                        },
                    )
                    await db.execute(
                        text(
                            "INSERT INTO participants "
                            "(id, room_id, user_id, role, joined_at) "
                            "VALUES (:id, :room_id, :user_id, 'member', :joined_at)"
                        ),
                        {
                            "id": str(uuid4()),
                            "room_id": room_id,
                            "user_id": user_id,
                            "joined_at": now,
                        },
                    )
                    await db.commit()
            finally:
                await engine.dispose()

            # Apply migration 052 (dedupe + indexes).
            await _alembic_action("upgrade", db_url, "head")

            engine = build_engine(db_url)
            try:
                async with engine.begin() as conn:
                    rows = (
                        await conn.execute(
                            text(
                                "SELECT role FROM participants WHERE user_id = :u"
                            ),
                            {"u": user_id},
                        )
                    ).all()
                    assert len(rows) == 1, "dedupe must collapse to one row"
                    assert rows[0][0] == "admin", "admin row must be the survivor"

                    with pytest.raises(IntegrityError):
                        await conn.execute(
                            text(
                                "INSERT INTO participants "
                                "(id, room_id, user_id, role, joined_at, pinned) "
                                "VALUES ('extra', :r, :u, 'member', "
                                "'2026-01-01T00:00:00+00:00', 0)"
                            ),
                            {"r": room_id, "u": user_id},
                        )
            finally:
                await engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass
