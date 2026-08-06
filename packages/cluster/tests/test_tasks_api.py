"""Integration tests for the /api/v1/.../tasks endpoints (#266).

Covers the *router-level* contract: that a (re)assignment to an agent
participant produces a synthetic mention message in the room, while
human or no-assignee paths leave the message log untouched. The
helper itself is unit-tested in ``test_tasks_injection.py``.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    Message,
    Participant,
    Project,
    Room,
    RoomAuthorizationAudit,
    Task,
    User,
)
from anygarden.messages.service import append_message


@pytest_asyncio.fixture()
async def tasks_env() -> AsyncIterator[dict]:
    """Spin up an app + DB + room with a creator user and an assignable agent."""
    config = AnygardenSettings(
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
    bystander_token = create_user_token(
        bystander.id,
        bystander.email,
        bystander.is_admin,
        secret=config.jwt_secret,
    )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "token": creator_token,
            "bystander_token": bystander_token,
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


class TestTaskInputValidation:
    """#471 — the task create/update schemas must reject meaningless
    input at the edge (422) instead of persisting it.

    Three gaps were open: an empty ``title`` on create, and an
    arbitrary ``status`` string on both create and update (the DB column
    is a free ``String(32)``). Status is validated against the canonical
    ``TASK_STATUS_VALUES`` so the enum stays single-sourced (the same set
    the MCP ``mark_task_status`` path uses — see #319 drift note)."""

    @pytest.mark.asyncio
    async def test_create_rejects_empty_title(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": ""},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rejects_unknown_status(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x", "status": "not-a-status"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_accepts_canonical_status(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x", "status": "done"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "done"

    @pytest.mark.asyncio
    async def test_update_rejects_unknown_status(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x"},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]
        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"status": "not-a-status"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_requires_claim_for_in_progress(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x"},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]
        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"status": "in_progress"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "TASK_CLAIM_REQUIRED"

    @pytest.mark.asyncio
    async def test_update_status_none_is_allowed(self, tasks_env) -> None:
        """A partial update that omits ``status`` (or sends null) must not
        trip the validator — only title/assignee changes."""
        client = tasks_env["client"]
        room = tasks_env["room"]
        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x"},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]
        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"title": "renamed"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "renamed"


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

        async with tasks_env["factory"]() as db:
            task = await db.get(Task, task_id)
            assert task is not None
            task.status = "in_progress"
            await db.commit()

        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"status": "done"},
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


class TestAssignedAt:
    """#314 — ``assigned_at`` is the sweeper's pickup-timeout clock.
    Must be stamped on creation when an assignee is present, refreshed
    on every reassignment, and left NULL when no one is assigned (so
    the sweeper's IS NOT NULL guard skips the row)."""

    @pytest.mark.asyncio
    async def test_create_with_assignee_stamps_assigned_at(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        a_pid = tasks_env["agent_a_p_id"]

        resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x", "assignee_participant_id": a_pid},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        async with tasks_env["factory"]() as db:
            from anygarden.db.models import Task as TaskRow

            row = (
                await db.execute(select(TaskRow).where(TaskRow.id == task_id))
            ).scalar_one()
            assert row.assigned_at is not None

    @pytest.mark.asyncio
    async def test_create_without_assignee_leaves_assigned_at_null(
        self, tasks_env
    ) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]

        resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "later"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        async with tasks_env["factory"]() as db:
            from anygarden.db.models import Task as TaskRow

            row = (
                await db.execute(select(TaskRow).where(TaskRow.id == task_id))
            ).scalar_one()
            assert row.assigned_at is None

    @pytest.mark.asyncio
    async def test_assigning_after_create_stamps_assigned_at(self, tasks_env) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        a_pid = tasks_env["agent_a_p_id"]

        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x"},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]

        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"assignee_participant_id": a_pid},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 200

        async with tasks_env["factory"]() as db:
            from anygarden.db.models import Task as TaskRow

            row = (
                await db.execute(select(TaskRow).where(TaskRow.id == task_id))
            ).scalar_one()
            assert row.assigned_at is not None

    @pytest.mark.asyncio
    async def test_reassign_refreshes_assigned_at(self, tasks_env) -> None:
        """Each reassignment is a fresh pickup window for the new
        assignee — the previous assignee's timer must not carry over."""
        import asyncio

        client = tasks_env["client"]
        room = tasks_env["room"]
        a_pid = tasks_env["agent_a_p_id"]
        b_pid = tasks_env["agent_b_p_id"]

        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x", "assignee_participant_id": a_pid},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]

        async with tasks_env["factory"]() as db:
            from anygarden.db.models import Task as TaskRow

            first = (
                await db.execute(select(TaskRow).where(TaskRow.id == task_id))
            ).scalar_one()
            t1 = first.assigned_at

        await asyncio.sleep(0.01)  # ensure timestamp moves forward
        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"assignee_participant_id": b_pid},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 200

        async with tasks_env["factory"]() as db:
            from anygarden.db.models import Task as TaskRow

            second = (
                await db.execute(select(TaskRow).where(TaskRow.id == task_id))
            ).scalar_one()
            t2 = second.assigned_at

        assert t1 is not None and t2 is not None
        assert t2 > t1

    @pytest.mark.asyncio
    async def test_status_only_change_does_not_touch_assigned_at(
        self, tasks_env
    ) -> None:
        client = tasks_env["client"]
        room = tasks_env["room"]
        a_pid = tasks_env["agent_a_p_id"]

        create = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "x", "assignee_participant_id": a_pid},
            headers=_auth(tasks_env["token"]),
        )
        task_id = create.json()["id"]

        async with tasks_env["factory"]() as db:
            before = (
                await db.execute(select(Task).where(Task.id == task_id))
            ).scalar_one()
            t1 = before.assigned_at
            before.status = "in_progress"
            await db.commit()

        resp = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"status": "done"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 200

        async with tasks_env["factory"]() as db:
            from anygarden.db.models import Task as TaskRow

            after = (
                await db.execute(select(TaskRow).where(TaskRow.id == task_id))
            ).scalar_one()
            assert after.assigned_at == t1


class TestRestResolveWake:
    """#459 (Wave 2c) — the REST ``PUT /tasks/{id}`` terminal transition
    must run the same resolve-wake hook as the MCP ``mark_task_status``
    path: a task blocked by the just-completed one is returned to ``todo``
    and re-injected as a mention."""

    @pytest.mark.asyncio
    async def test_marking_blocker_done_via_rest_wakes_dependent(
        self, tasks_env
    ) -> None:
        from anygarden.db.models import Task as TaskRow
        from anygarden.db.models import TaskBlocker

        client = tasks_env["client"]
        room = tasks_env["room"]
        a_pid = tasks_env["agent_a_p_id"]

        # Create a blocker task and a dependent task (both agent-assigned).
        blocker_resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "blocker", "assignee_participant_id": a_pid},
            headers=_auth(tasks_env["token"]),
        )
        dep_resp = await client.post(
            f"/api/v1/rooms/{room.id}/tasks",
            json={"title": "dependent", "assignee_participant_id": a_pid},
            headers=_auth(tasks_env["token"]),
        )
        blocker_id = blocker_resp.json()["id"]
        dep_id = dep_resp.json()["id"]

        # Wire the edge directly (no REST add endpoint) and park the
        # dependent in ``blocked``.
        async with tasks_env["factory"]() as db:
            db.add(TaskBlocker(task_id=dep_id, blocked_by_task_id=blocker_id))
            blocker_row = (
                await db.execute(select(TaskRow).where(TaskRow.id == blocker_id))
            ).scalar_one()
            blocker_row.status = "in_progress"
            dep_row = (
                await db.execute(select(TaskRow).where(TaskRow.id == dep_id))
            ).scalar_one()
            dep_row.status = "blocked"
            await db.commit()

        msgs_before = await _count_task_messages(tasks_env["factory"], room.id)

        # Mark the blocker done via REST — resolve-wake should fire.
        resp = await client.put(
            f"/api/v1/tasks/{blocker_id}",
            json={"status": "done"},
            headers=_auth(tasks_env["token"]),
        )
        assert resp.status_code == 200

        async with tasks_env["factory"]() as db:
            dep_after = (
                await db.execute(select(TaskRow).where(TaskRow.id == dep_id))
            ).scalar_one()
            assert dep_after.status == "todo"
            # Satisfied edge cleared.
            edges = (
                await db.execute(
                    select(TaskBlocker).where(TaskBlocker.task_id == dep_id)
                )
            ).scalars().all()
            assert edges == []

        # A fresh re-wake mention was injected for the dependent.
        msgs_after = await _count_task_messages(tasks_env["factory"], room.id)
        assert msgs_after == msgs_before + 1


class TestMessageLinkedTasksAndClaims:
    async def _source_messages(self, tasks_env) -> tuple[Message, Message]:
        async with tasks_env["factory"]() as db:
            room = tasks_env["room"]
            root = await append_message(
                db,
                room.id,
                tasks_env["creator_p_id"],
                "source root",
            )
            reply = await append_message(
                db,
                room.id,
                tasks_env["creator_p_id"],
                "source reply",
                thread_root_id=root.id,
            )
            await db.commit()
            return root, reply

    @pytest.mark.asyncio
    async def test_reply_source_derives_root_and_assignment_stays_in_thread(
        self, tasks_env
    ) -> None:
        root, reply = await self._source_messages(tasks_env)
        response = await tasks_env["client"].post(
            f"/api/v1/rooms/{tasks_env['room'].id}/messages/{reply.id}/task",
            json={
                "title": "reply work",
                "assignee_participant_id": tasks_env["agent_a_p_id"],
            },
            headers=_auth(tasks_env["token"]),
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["source_message_id"] == reply.id
        assert payload["source_thread_root_id"] == root.id

        assignment = await _last_task_message(
            tasks_env["factory"], tasks_env["room"].id
        )
        assert assignment is not None
        assert assignment.parent_message_id == root.id
        assert assignment.root_message_id == root.id

        duplicate = await tasks_env["client"].post(
            f"/api/v1/rooms/{tasks_env['room'].id}/messages/{reply.id}/task",
            json={"title": "duplicate"},
            headers=_auth(tasks_env["token"]),
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == {
            "code": "TASK_SOURCE_ALREADY_LINKED",
            "existing_task_id": payload["id"],
        }

        deleted = await tasks_env["client"].delete(
            f"/api/v1/tasks/{payload['id']}",
            headers=_auth(tasks_env["token"]),
        )
        assert deleted.status_code == 409
        assert (
            deleted.json()["detail"]["code"]
            == "TASK_SOURCE_LINKED_DELETE_FORBIDDEN"
        )

    @pytest.mark.asyncio
    async def test_cross_room_and_system_sources_are_rejected(self, tasks_env) -> None:
        async with tasks_env["factory"]() as db:
            other = Room(name="other")
            db.add(other)
            await db.flush()
            cross = await append_message(db, other.id, None, "private source")
            system = await append_message(
                db,
                tasks_env["room"].id,
                None,
                "generated",
                {"system_origin": "routing"},
            )
            await db.commit()

        cross_response = await tasks_env["client"].post(
            f"/api/v1/rooms/{tasks_env['room'].id}/messages/{cross.id}/task",
            json={"title": "cross"},
            headers=_auth(tasks_env["token"]),
        )
        assert cross_response.status_code == 404
        assert cross_response.json()["detail"] == {
            "code": "TASK_SOURCE_MESSAGE_NOT_FOUND",
            "message": "Message not found",
            "detail": "Message not found",
        }

        system_response = await tasks_env["client"].post(
            f"/api/v1/rooms/{tasks_env['room'].id}/messages/{system.id}/task",
            json={"title": "system"},
            headers=_auth(tasks_env["token"]),
        )
        assert system_response.status_code == 400
        assert (
            system_response.json()["detail"]["code"]
            == "TASK_SYSTEM_SOURCE_FORBIDDEN"
        )

    @pytest.mark.asyncio
    async def test_atomic_claim_has_exactly_one_winner_and_no_chat_wake(
        self, tasks_env
    ) -> None:
        async with tasks_env["factory"]() as db:
            room = await db.get(Room, tasks_env["room"].id)
            assert room is not None
            room.allow_human_assignment = True
            await db.commit()

        created = await tasks_env["client"].post(
            f"/api/v1/rooms/{tasks_env['room'].id}/tasks",
            json={"title": "race"},
            headers=_auth(tasks_env["token"]),
        )
        task_id = created.json()["id"]
        before = await _count_task_messages(
            tasks_env["factory"], tasks_env["room"].id
        )

        first, second = await asyncio.gather(
            tasks_env["client"].post(
                f"/api/v1/tasks/{task_id}/claim",
                headers=_auth(tasks_env["token"]),
            ),
            tasks_env["client"].post(
                f"/api/v1/tasks/{task_id}/claim",
                headers=_auth(tasks_env["bystander_token"]),
            ),
        )
        assert sorted((first.status_code, second.status_code)) == [200, 409]
        winner = first if first.status_code == 200 else second
        loser = first if first.status_code == 409 else second
        assert loser.json()["detail"]["code"] == "TASK_CLAIM_CONFLICT"
        assert loser.json()["detail"]["current_status"] == "in_progress"
        assert (
            loser.json()["detail"]["current_assignee_participant_id"]
            == winner.json()["assignee_participant_id"]
        )

        async with tasks_env["factory"]() as db:
            task = await db.get(Task, task_id)
            assert task is not None
            assert task.status == "in_progress"
            assert task.assignee_participant_id in {
                tasks_env["creator_p_id"],
                tasks_env["bystander_p_id"],
            }
            assert task.assigned_at is not None
            assert task.started_at is not None
        assert (
            await _count_task_messages(tasks_env["factory"], tasks_env["room"].id)
            == before
        )

    @pytest.mark.asyncio
    async def test_human_claim_gate_archive_and_removal_requeue(self, tasks_env) -> None:
        created = await tasks_env["client"].post(
            f"/api/v1/rooms/{tasks_env['room'].id}/tasks",
            json={"title": "human work"},
            headers=_auth(tasks_env["token"]),
        )
        task_id = created.json()["id"]
        disabled = await tasks_env["client"].post(
            f"/api/v1/tasks/{task_id}/claim",
            headers=_auth(tasks_env["bystander_token"]),
        )
        assert disabled.status_code == 403

        async with tasks_env["factory"]() as db:
            room = await db.get(Room, tasks_env["room"].id)
            assert room is not None
            room.allow_human_assignment = True
            await db.commit()
        claimed = await tasks_env["client"].post(
            f"/api/v1/tasks/{task_id}/claim",
            headers=_auth(tasks_env["bystander_token"]),
        )
        assert claimed.status_code == 200
        blocked = await tasks_env["client"].put(
            f"/api/v1/tasks/{task_id}",
            json={"status": "blocked"},
            headers=_auth(tasks_env["bystander_token"]),
        )
        assert blocked.status_code == 200
        requeued = await tasks_env["client"].post(
            f"/api/v1/tasks/{task_id}/requeue",
            json={"reason": "blocker cleared"},
            headers=_auth(tasks_env["token"]),
        )
        assert requeued.status_code == 200
        reclaimed = await tasks_env["client"].post(
            f"/api/v1/tasks/{task_id}/claim",
            headers=_auth(tasks_env["bystander_token"]),
        )
        assert reclaimed.status_code == 200

        removed = await tasks_env["client"].delete(
            f"/api/v1/rooms/{tasks_env['room'].id}/participants/"
            f"{tasks_env['bystander_p_id']}",
            headers=_auth(tasks_env["token"]),
        )
        assert removed.status_code == 204
        async with tasks_env["factory"]() as db:
            task = await db.get(Task, task_id)
            assert task is not None
            assert task.status == "todo"
            assert task.assignee_participant_id is None
            assert task.error == "assignee_removed"
            room = await db.get(Room, tasks_env["room"].id)
            assert room is not None
            room.archived_at = task.created_at
            await db.commit()

        archived = await tasks_env["client"].post(
            f"/api/v1/tasks/{task_id}/claim",
            headers=_auth(tasks_env["token"]),
        )
        assert archived.status_code == 409

    @pytest.mark.asyncio
    async def test_admin_requeue_records_reason(self, tasks_env) -> None:
        created = await tasks_env["client"].post(
            f"/api/v1/rooms/{tasks_env['room'].id}/tasks",
            json={
                "title": "retry",
                "assignee_participant_id": tasks_env["agent_a_p_id"],
            },
            headers=_auth(tasks_env["token"]),
        )
        task_id = created.json()["id"]
        async with tasks_env["factory"]() as db:
            task = await db.get(Task, task_id)
            assert task is not None
            task.status = "in_progress"
            await db.commit()

        response = await tasks_env["client"].post(
            f"/api/v1/tasks/{task_id}/requeue",
            json={
                "reason": "agent unavailable",
                "assignee_participant_id": tasks_env["agent_b_p_id"],
            },
            headers=_auth(tasks_env["token"]),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "todo"
        assert response.json()["assignee_participant_id"] == tasks_env["agent_b_p_id"]
        async with tasks_env["factory"]() as db:
            audit = await db.scalar(
                select(RoomAuthorizationAudit).where(
                    RoomAuthorizationAudit.scope == "task.requeue"
                )
            )
            assert audit is not None
            assert audit.details["reason"] == "agent unavailable"
