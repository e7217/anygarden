"""Unit tests for ``doorae.rooms.membership`` helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Agent, Participant, Project, Room, User
from doorae.rooms.membership import add_user_to_room, ensure_agent_in_room
from doorae.ws.protocol import JoinRoomOut, RoomMembershipChangedOut


@dataclass
class _Send:
    participant_id: str
    frame: Any


@dataclass
class FakeConnectionManager:
    """Records ``send_to`` calls without opening real sockets."""

    sends: list[_Send] = field(default_factory=list)

    async def send_to(self, participant_id: str, frame: Any) -> None:
        self.sends.append(_Send(participant_id=participant_id, frame=frame))


@pytest.fixture()
def manager() -> FakeConnectionManager:
    return FakeConnectionManager()


async def _seed_agent(db: AsyncSession) -> tuple[Project, Agent]:
    project = Project(name="p")
    db.add(project)
    await db.flush()
    agent = Agent(name="a", engine="codex", actual_state="running")
    db.add(agent)
    await db.flush()
    await db.commit()
    await db.refresh(project)
    await db.refresh(agent)
    return project, agent


async def _seed_user(db: AsyncSession) -> tuple[Project, User]:
    project = Project(name="p")
    db.add(project)
    await db.flush()
    user = User(email="u@test.com", password_hash="x")
    db.add(user)
    await db.flush()
    await db.commit()
    await db.refresh(project)
    await db.refresh(user)
    return project, user


async def _make_room(db: AsyncSession, project_id: str, name: str) -> Room:
    room = Room(project_id=project_id, name=name)
    db.add(room)
    await db.flush()
    await db.commit()
    await db.refresh(room)
    return room


class TestEnsureAgentInRoom:
    @pytest.mark.asyncio
    async def test_creates_row_and_sends_no_frame_when_no_other_pid(
        self, db: AsyncSession, manager: FakeConnectionManager
    ) -> None:
        """First-ever Participant for this agent → no 'other' pids to
        notify, so no JoinRoomOut goes out. The row is still created."""
        project, agent = await _seed_agent(db)
        room = await _make_room(db, project.id, "r1")

        part, created = await ensure_agent_in_room(
            db, manager, room_id=room.id, agent_id=agent.id
        )
        assert created is True
        assert part.agent_id == agent.id
        assert part.room_id == room.id
        assert manager.sends == []

    @pytest.mark.asyncio
    async def test_idempotent_second_call_reuses_row(
        self, db: AsyncSession, manager: FakeConnectionManager
    ) -> None:
        project, agent = await _seed_agent(db)
        room = await _make_room(db, project.id, "r1")

        _, created_first = await ensure_agent_in_room(
            db, manager, room_id=room.id, agent_id=agent.id
        )
        _, created_second = await ensure_agent_in_room(
            db, manager, room_id=room.id, agent_id=agent.id
        )
        assert created_first is True
        assert created_second is False

        count = (
            await db.execute(
                select(Participant).where(
                    Participant.room_id == room.id,
                    Participant.agent_id == agent.id,
                )
            )
        ).scalars().all()
        assert len(count) == 1

    @pytest.mark.asyncio
    async def test_sends_joinroom_to_other_pids_always(
        self, db: AsyncSession, manager: FakeConnectionManager
    ) -> None:
        """Core invariant for issue #50: JoinRoomOut must be pushed on
        *every* call, not only when ``created`` is True."""
        project, agent = await _seed_agent(db)
        room_a = await _make_room(db, project.id, "a")
        room_b = await _make_room(db, project.id, "b")

        # Seed agent as Participant of room_a first — this gives the
        # helper an "other pid" to push to when we later add it to
        # room_b.
        part_a = Participant(room_id=room_a.id, agent_id=agent.id, role="member")
        db.add(part_a)
        await db.commit()
        await db.refresh(part_a)

        manager.sends.clear()
        await ensure_agent_in_room(
            db, manager, room_id=room_b.id, agent_id=agent.id
        )
        assert len(manager.sends) == 1
        assert manager.sends[0].participant_id == part_a.id
        assert isinstance(manager.sends[0].frame, JoinRoomOut)
        assert manager.sends[0].frame.room_id == room_b.id

        # Second call is idempotent at DB level but MUST still
        # broadcast the frame (bug #50 was the missed notification on
        # the already-a-member branch).
        manager.sends.clear()
        await ensure_agent_in_room(
            db, manager, room_id=room_b.id, agent_id=agent.id
        )
        assert len(manager.sends) == 1
        assert manager.sends[0].frame.room_id == room_b.id

    @pytest.mark.asyncio
    async def test_manager_none_skips_notification(
        self, db: AsyncSession
    ) -> None:
        project, agent = await _seed_agent(db)
        room = await _make_room(db, project.id, "r1")

        part, created = await ensure_agent_in_room(
            db, None, room_id=room.id, agent_id=agent.id
        )
        assert created is True
        assert part.room_id == room.id


class TestAddUserToRoom:
    @pytest.mark.asyncio
    async def test_inserts_row(
        self, db: AsyncSession, manager: FakeConnectionManager
    ) -> None:
        project, user = await _seed_user(db)
        room = await _make_room(db, project.id, "r1")

        part = await add_user_to_room(
            db, manager, room_id=room.id, user_id=user.id
        )
        assert part.user_id == user.id
        assert part.room_id == room.id
        assert manager.sends == []

    @pytest.mark.asyncio
    async def test_broadcasts_added_to_other_user_pids(
        self, db: AsyncSession, manager: FakeConnectionManager
    ) -> None:
        project, user = await _seed_user(db)
        room_a = await _make_room(db, project.id, "a")
        room_b = await _make_room(db, project.id, "b")

        existing = Participant(room_id=room_a.id, user_id=user.id, role="member")
        db.add(existing)
        await db.commit()
        await db.refresh(existing)

        manager.sends.clear()
        await add_user_to_room(
            db, manager, room_id=room_b.id, user_id=user.id
        )
        assert len(manager.sends) == 1
        sent = manager.sends[0]
        assert sent.participant_id == existing.id
        assert isinstance(sent.frame, RoomMembershipChangedOut)
        assert sent.frame.action == "added"
        assert sent.frame.room_id == room_b.id
        assert sent.frame.user_id == user.id

    @pytest.mark.asyncio
    async def test_manager_none_skips_notification(
        self, db: AsyncSession
    ) -> None:
        project, user = await _seed_user(db)
        room = await _make_room(db, project.id, "r1")

        part = await add_user_to_room(
            db, None, room_id=room.id, user_id=user.id
        )
        assert part.user_id == user.id
