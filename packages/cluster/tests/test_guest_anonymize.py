"""Tests for the guest-lifecycle anonymisation job.

§11.10 of the RFC. The job walks guest User rows whose backing
``RoomInviteLink`` is revoked or expired beyond the grace window
and scrubs their ``display_name`` in place.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select

from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Base,
    Participant,
    Project,
    Room,
    RoomInviteLink,
    User,
)
from doorae.guest.anonymize import (
    ANON_DISPLAY_NAME,
    anonymize_expired_guests,
)


@pytest_asyncio.fixture()
async def db_env(config: DooraeSettings) -> AsyncIterator[dict]:
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield {"session_factory": session_factory}

    await engine.dispose()


async def _seed_guest(
    session_factory,
    *,
    display_name: str,
    invite_revoked_at: datetime | None = None,
    invite_expires_at: datetime | None = None,
) -> str:
    """Build one guest User + Participant whose backing invite has
    the supplied revoked_at / expires_at timestamps. Returns the
    guest user's id for assertions."""
    async with session_factory() as db:
        owner = User(email=f"o-{display_name}@doorae.io", password_hash="x")
        db.add(owner)
        await db.flush()
        project = Project(name=f"p-{display_name}")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name=f"r-{display_name}")
        db.add(room)
        await db.flush()
        invite = RoomInviteLink(
            room_id=room.id,
            created_by_user_id=owner.id,
            token_hash="hash",
            lookup_hint="inv_hint1234",
            revoked_at=invite_revoked_at,
            expires_at=invite_expires_at,
        )
        db.add(invite)
        guest = User(
            email=None,
            password_hash=None,
            is_anonymous=True,
            display_name=display_name,
        )
        db.add(guest)
        await db.flush()
        db.add(
            Participant(
                room_id=room.id, user_id=guest.id, role="member"
            )
        )
        await db.commit()
        await db.refresh(guest)
        return guest.id


async def _read_display_name(session_factory, user_id: str) -> str | None:
    async with session_factory() as db:
        row = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        return row.display_name


class TestAnonymize:
    @pytest.mark.asyncio
    async def test_revoked_beyond_grace_is_scrubbed(self, db_env) -> None:
        now = datetime.now(timezone.utc)
        guest_id = await _seed_guest(
            db_env["session_factory"],
            display_name="Alice",
            invite_revoked_at=now - timedelta(days=45),
        )
        async with db_env["session_factory"]() as db:
            count = await anonymize_expired_guests(db, now=now)
        assert count == 1
        assert await _read_display_name(db_env["session_factory"], guest_id) == ANON_DISPLAY_NAME

    @pytest.mark.asyncio
    async def test_expired_beyond_grace_is_scrubbed(self, db_env) -> None:
        now = datetime.now(timezone.utc)
        guest_id = await _seed_guest(
            db_env["session_factory"],
            display_name="Bob",
            invite_expires_at=now - timedelta(days=45),
        )
        async with db_env["session_factory"]() as db:
            count = await anonymize_expired_guests(db, now=now)
        assert count == 1
        assert await _read_display_name(db_env["session_factory"], guest_id) == ANON_DISPLAY_NAME

    @pytest.mark.asyncio
    async def test_fresh_revoked_is_untouched(self, db_env) -> None:
        """Inside the grace window — the job must NOT scrub."""
        now = datetime.now(timezone.utc)
        guest_id = await _seed_guest(
            db_env["session_factory"],
            display_name="Carla",
            invite_revoked_at=now - timedelta(days=1),
        )
        async with db_env["session_factory"]() as db:
            count = await anonymize_expired_guests(db, now=now)
        assert count == 0
        assert await _read_display_name(db_env["session_factory"], guest_id) == "Carla"

    @pytest.mark.asyncio
    async def test_active_invite_is_untouched(self, db_env) -> None:
        """No revoked_at, no expires_at → invite still usable, guest stays."""
        guest_id = await _seed_guest(
            db_env["session_factory"],
            display_name="Diego",
        )
        async with db_env["session_factory"]() as db:
            count = await anonymize_expired_guests(db)
        assert count == 0
        assert await _read_display_name(db_env["session_factory"], guest_id) == "Diego"

    @pytest.mark.asyncio
    async def test_idempotent(self, db_env) -> None:
        now = datetime.now(timezone.utc)
        guest_id = await _seed_guest(
            db_env["session_factory"],
            display_name="Ellie",
            invite_revoked_at=now - timedelta(days=45),
        )
        async with db_env["session_factory"]() as db:
            first = await anonymize_expired_guests(db, now=now)
            # Second pass on the same DB state finds nothing new.
            second = await anonymize_expired_guests(db, now=now)
        assert first == 1
        assert second == 0
        assert await _read_display_name(db_env["session_factory"], guest_id) == ANON_DISPLAY_NAME

    @pytest.mark.asyncio
    async def test_does_not_scrub_registered_users(self, db_env) -> None:
        """Registered users share the User table; they must be ignored
        regardless of any related invite state."""
        now = datetime.now(timezone.utc)
        async with db_env["session_factory"]() as db:
            u = User(email="u@doorae.io", password_hash="x", is_anonymous=False)
            db.add(u)
            await db.commit()
            await db.refresh(u)
            user_id = u.id

        # Also seed a genuinely expired guest so the job has work to do.
        await _seed_guest(
            db_env["session_factory"],
            display_name="Frank",
            invite_expires_at=now - timedelta(days=45),
        )
        async with db_env["session_factory"]() as db:
            await anonymize_expired_guests(db, now=now)

        async with db_env["session_factory"]() as db:
            fetched = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one()
            # Registered user row unchanged — email + no display_name.
            assert fetched.email == "u@doorae.io"
            assert fetched.display_name is None
