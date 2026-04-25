"""Integration tests for the /api/v1/.../tasks endpoints (#266).

Covers the *router-level* contract: that a (re)assignment to an agent
participant produces a synthetic mention message in the room, while
human or no-assignee paths leave the message log untouched. The
helper itself is unit-tested in ``test_tasks_injection.py``.
"""

from __future__ import annotations

import secrets
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent,
    Base,
    Message,
    Participant,
    Project,
    Room,
    User,
)


@pytest_asyncio.fixture()
async def tasks_env() -> AsyncIterator[dict]:
    """Spin up an app + DB + room with a creator user and an assignable agent."""
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        creator = User(email="creator@test.com", password_hash="x", is_admin=True)
        bystander = User(email="bystander@test.com", password_hash="x")
        db.add_all([creator, bystander])
        await db.flush()

        agent_a = Agent(name="bot-A", engine="echo")
        agent_b = Agent(name="bot-B", engine="echo")
        db.add_all([agent_a, agent_b])
        await db.flush()

        project = Project(name="p")
        db.add(project)
        await db.flush()

        room = Room(name="r", project_id=project.id)
        db.add(room)
        await db.flush()

        creator_p = Participant(room_id=room.id, user_id=creator.id, role="member")
        bystander_p = Participant(room_id=room.id, user_id=bystander.id, role="member")
        agent_a_p = Participant(room_id=room.id, agent_id=agent_a.id, role="member")
        agent_b_p = Participant(room_id=room.id, agent_id=agent_b.id, role="member")
        db.add_all([creator_p, bystander_p, agent_a_p, agent_b_p])
        await db.commit()

    creator_token = create_user_token(
        creator.id, creator.email, creator.is_admin, secret=config.jwt_secret
    )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "token": creator_token,
            "factory": factory,
            "room": room,
            "creator_p_id": creator_p.id,
            "bystander_p_id": bystander_p.id,
            "agent_a_p_id": agent_a_p.id,
            "agent_b_p_id": agent_b_p.id,
        }

    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _count_task_messages(factory, room_id: str) -> int:
    """Count messages in *room_id* that carry the task_assignment marker."""
    async with factory() as db:
        rows = (
            await db.execute(select(Message).where(Message.room_id == room_id))
        ).scalars().all()
        return sum(
            1
            for m in rows
            if (m.extra_metadata or {}).get("task_assignment") is not None
        )


async def _last_task_message(factory, room_id: str) -> Message | None:
    async with factory() as db:
        rows = (
            await db.execute(
                select(Message)
                .where(Message.room_id == room_id)
                .order_by(Message.seq.desc())
            )
        ).scalars().all()
        for m in rows:
            if (m.extra_metadata or {}).get("task_assignment") is not None:
                return m
        return None


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_create_with_agent_assignee_injects_mention_message(
        self, tasks_env
    ) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        agent_p_id = tasks_env["agent_a_p_id"]

        resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "design review", "assignee_participant_id": agent_p_id},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 201

        msg = await _last_task_message(tasks_env["factory"], room.id)
        assert msg is not None
        assert f"<@user:{agent_p_id}>" in msg.content
        assert "[TASK]" in msg.content
        assert "design review" in msg.content
        meta = msg.extra_metadata
        assert meta["mentions"] == [{"type": "user", "id": agent_p_id}]
        assert meta["task_assignment"]["assignee_pid"] == agent_p_id
        assert meta["task_assignment"]["event"] == "assigned"

    @pytest.mark.asyncio
    async def test_create_with_human_assignee_does_not_inject(
        self, tasks_env
    ) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        human_p_id = tasks_env["bystander_p_id"]

        resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "manual cleanup", "assignee_participant_id": human_p_id},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 201
        assert await _count_task_messages(tasks_env["factory"], room.id) == 0

    @pytest.mark.asyncio
    async def test_create_without_assignee_does_not_inject(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]

        resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "stub"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 201
        assert await _count_task_messages(tasks_env["factory"], room.id) == 0

    @pytest.mark.asyncio
    async def test_create_rejects_assignee_from_other_room(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        # Build a participant in another room
        async with tasks_env["factory"]() as db:
            other_room = Room(name="other")
            db.add(other_room)
            await db.flush()
            user = User(email="other@test.com", password_hash="x")
            db.add(user)
            await db.flush()
            outside = Participant(
                room_id=other_room.id, user_id=user.id, role="member"
            )
            db.add(outside)
            await db.commit()
            outside_id = outside.id

        resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x", "assignee_participant_id": outside_id},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 400


class TestUpdateTask:
    @pytest.mark.asyncio
    async def test_assigning_agent_after_create_injects(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]

        # Step 1: create without assignee
        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "later"},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]
        assert await _count_task_messages(tasks_env["factory"], room.id) == 0

        # Step 2: assign to an agent
        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"assignee_participant_id": tasks_env["agent_a_p_id"]},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 200
        msg = await _last_task_message(tasks_env["factory"], room.id)
        assert msg is not None
        assert msg.extra_metadata["task_assignment"]["event"] == "assigned"

    @pytest.mark.asyncio
    async def test_reassigning_to_different_agent_uses_reassigned_event(
        self, tasks_env
    ) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        a_pid = tasks_env["agent_a_p_id"]
        b_pid = tasks_env["agent_b_p_id"]

        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "swap", "assignee_participant_id": a_pid},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]
        assert await _count_task_messages(tasks_env["factory"], room.id) == 1

        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"assignee_participant_id": b_pid},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 200
        assert await _count_task_messages(tasks_env["factory"], room.id) == 2
        msg = await _last_task_message(tasks_env["factory"], room.id)
        assert msg.extra_metadata["task_assignment"]["event"] == "reassigned"
        assert msg.extra_metadata["task_assignment"]["assignee_pid"] == b_pid

    @pytest.mark.asyncio
    async def test_status_only_change_does_not_inject(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        a_pid = tasks_env["agent_a_p_id"]

        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x", "assignee_participant_id": a_pid},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]
        assert await _count_task_messages(tasks_env["factory"], room.id) == 1

        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"status": "in_progress"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 200
        # Still only the original injection, no extra one for status.
        assert await _count_task_messages(tasks_env["factory"], room.id) == 1

    @pytest.mark.asyncio
    async def test_reassigning_to_human_does_not_inject(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        a_pid = tasks_env["agent_a_p_id"]
        human_pid = tasks_env["bystander_p_id"]

        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x", "assignee_participant_id": a_pid},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]
        assert await _count_task_messages(tasks_env["factory"], room.id) == 1

        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"assignee_participant_id": human_pid},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 200
        # Reassigning to a human does NOT trigger another injection.
        assert await _count_task_messages(tasks_env["factory"], room.id) == 1
