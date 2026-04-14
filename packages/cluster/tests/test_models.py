"""Tests for ORM model CRUD and constraints."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import (
    Agent,
    AgentFile,
    Machine,
    Message,
    Participant,
    Project,
    Room,
    User,
)
from doorae.db.repository import append_message, replay_since_seq


# ── Helper to create common fixtures ──────────────────────────────────


async def _make_user(db: AsyncSession, email: str = "test@example.com") -> User:
    user = User(email=email, password_hash="placeholder")
    db.add(user)
    await db.flush()
    return user


async def _make_project(db: AsyncSession, name: str = "TestProject") -> Project:
    project = Project(name=name)
    db.add(project)
    await db.flush()
    return project


async def _make_room(db: AsyncSession, project_id: str, name: str = "general") -> Room:
    room = Room(project_id=project_id, name=name)
    db.add(room)
    await db.flush()
    return room


# ── CRUD Tests ────────────────────────────────────────────────────────


class TestProjectCRUD:
    @pytest.mark.asyncio
    async def test_create_project(self, db: AsyncSession) -> None:
        p = await _make_project(db, "My Project")
        assert p.id is not None
        assert p.name == "My Project"

    @pytest.mark.asyncio
    async def test_project_has_created_at(self, db: AsyncSession) -> None:
        p = await _make_project(db)
        assert p.created_at is not None


class TestUserCRUD:
    @pytest.mark.asyncio
    async def test_create_user(self, db: AsyncSession) -> None:
        u = await _make_user(db, "alice@doorae.io")
        result = await db.execute(select(User).where(User.id == u.id))
        fetched = result.scalar_one()
        assert fetched.email == "alice@doorae.io"

    @pytest.mark.asyncio
    async def test_user_email_unique(self, db: AsyncSession) -> None:
        await _make_user(db, "dup@doorae.io")
        db.add(User(email="dup@doorae.io", password_hash="x"))
        with pytest.raises(IntegrityError):
            await db.flush()


class TestRoomCRUD:
    @pytest.mark.asyncio
    async def test_create_room(self, db: AsyncSession) -> None:
        p = await _make_project(db)
        r = await _make_room(db, p.id, "dev-chat")
        assert r.project_id == p.id
        assert r.name == "dev-chat"

    @pytest.mark.asyncio
    async def test_room_parent_self_ref(self, db: AsyncSession) -> None:
        p = await _make_project(db)
        parent = await _make_room(db, p.id, "parent")
        child = Room(project_id=p.id, name="child", parent_room_id=parent.id)
        db.add(child)
        await db.flush()
        assert child.parent_room_id == parent.id


class TestMachineCRUD:
    @pytest.mark.asyncio
    async def test_create_machine(self, db: AsyncSession) -> None:
        user = await _make_user(db, "owner@doorae.io")
        machine = Machine(
            name="dev-box",
            hostname="dev.local",
            owner_user_id=user.id,
            cpu_cores=8,
            memory_gb=32.0,
            max_agents=4,
        )
        db.add(machine)
        await db.flush()
        assert machine.id is not None
        assert machine.owner_user_id == user.id


class TestAgentCRUD:
    @pytest.mark.asyncio
    async def test_create_agent(self, db: AsyncSession) -> None:
        agent = Agent(name="coder-bot", engine="gpt-4o")
        db.add(agent)
        await db.flush()
        assert agent.id is not None
        assert agent.engine == "gpt-4o"

    @pytest.mark.asyncio
    async def test_agent_agents_md_is_nullable(self, db: AsyncSession) -> None:
        agent = Agent(name="a", engine="codex")
        db.add(agent)
        await db.flush()
        assert agent.agents_md is None

        agent.agents_md = "# Agent instructions\nHello."
        await db.flush()
        refreshed = (
            await db.execute(select(Agent).where(Agent.id == agent.id))
        ).scalar_one()
        assert refreshed.agents_md.startswith("# Agent")


class TestAgentFileCRUD:
    @pytest.mark.asyncio
    async def test_create_agent_file(self, db: AsyncSession) -> None:
        agent = Agent(name="a", engine="codex")
        db.add(agent)
        await db.flush()

        row = AgentFile(
            agent_id=agent.id,
            path="skills/coder/SKILL.md",
            content="---\nname: coder\ndescription: x\n---\nbody",
        )
        db.add(row)
        await db.flush()
        assert row.id is not None
        assert row.updated_at is not None

    @pytest.mark.asyncio
    async def test_agent_file_unique_path_per_agent(
        self, db: AsyncSession
    ) -> None:
        agent = Agent(name="a", engine="codex")
        db.add(agent)
        await db.flush()

        db.add(AgentFile(agent_id=agent.id, path="skills/x/SKILL.md", content="v1"))
        await db.flush()
        db.add(AgentFile(agent_id=agent.id, path="skills/x/SKILL.md", content="v2"))
        with pytest.raises(IntegrityError):
            await db.flush()

    @pytest.mark.asyncio
    async def test_agent_file_cascades_on_agent_delete(
        self, db: AsyncSession
    ) -> None:
        agent = Agent(name="a", engine="codex")
        db.add(agent)
        await db.flush()
        db.add(AgentFile(agent_id=agent.id, path="skills/x/SKILL.md", content="v"))
        await db.flush()
        await db.commit()  # so the delete-cascade fires on its own transaction

        await db.delete(agent)
        await db.commit()

        remaining = (
            await db.execute(select(AgentFile).where(AgentFile.agent_id == agent.id))
        ).scalars().all()
        assert remaining == []


class TestParticipantCRUD:
    @pytest.mark.asyncio
    async def test_create_participant_with_user(self, db: AsyncSession) -> None:
        user = await _make_user(db, "part@doorae.io")
        proj = await _make_project(db)
        room = await _make_room(db, proj.id)
        p = Participant(room_id=room.id, user_id=user.id, role="member")
        db.add(p)
        await db.flush()
        assert p.user_id == user.id
        assert p.agent_id is None


class TestMessageAndSeq:
    @pytest.mark.asyncio
    async def test_append_message_assigns_seq(self, db: AsyncSession) -> None:
        user = await _make_user(db, "msg@doorae.io")
        proj = await _make_project(db)
        room = await _make_room(db, proj.id)
        part = Participant(room_id=room.id, user_id=user.id)
        db.add(part)
        await db.flush()

        m1 = await append_message(db, room.id, part.id, "hello")
        m2 = await append_message(db, room.id, part.id, "world")
        assert m1.seq == 1
        assert m2.seq == 2

    @pytest.mark.asyncio
    async def test_replay_since_seq(self, db: AsyncSession) -> None:
        user = await _make_user(db, "replay@doorae.io")
        proj = await _make_project(db)
        room = await _make_room(db, proj.id)
        part = Participant(room_id=room.id, user_id=user.id)
        db.add(part)
        await db.flush()

        for i in range(5):
            await append_message(db, room.id, part.id, f"msg-{i}")

        msgs = await replay_since_seq(db, room.id, since_seq=2)
        assert len(msgs) == 3
        assert msgs[0].seq == 3

    @pytest.mark.asyncio
    async def test_seq_unique_per_room(self, db: AsyncSession) -> None:
        """Messages in different rooms can share the same seq number."""
        user = await _make_user(db, "seq@doorae.io")
        proj = await _make_project(db)
        r1 = await _make_room(db, proj.id, "room-a")
        r2 = await _make_room(db, proj.id, "room-b")
        p1 = Participant(room_id=r1.id, user_id=user.id)
        p2 = Participant(room_id=r2.id, user_id=user.id)
        db.add_all([p1, p2])
        await db.flush()

        m1 = await append_message(db, r1.id, p1.id, "a")
        m2 = await append_message(db, r2.id, p2.id, "b")
        # Both should be seq=1 in their respective rooms
        assert m1.seq == 1
        assert m2.seq == 1
