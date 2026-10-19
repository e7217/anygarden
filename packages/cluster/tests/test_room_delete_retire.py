"""ROOM-11 (#911) — a room with run history can be deleted.

Deleting archives the room and hides it from listings; messages,
participants and invocation accounting stay. Open work in the room is
cancelled in the same transaction.
"""

from __future__ import annotations

import pytest
from anygarden.auth.jwt import create_user_token
from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    Participant,
    ProjectExecution,
    Room,
    User,
)
from anygarden.db.native_invocation_models import NativeInvocationAccounting
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from .test_durable_turns import FakeManager, _create_and_deliver
from .test_durable_turns import turn_env as turn_env
from .test_project_execution_flow import harness as harness
from .test_rooms import room_env as room_env
from .test_turn_cancel import _client

pytestmark = [pytest.mark.asyncio, pytest.mark.req("ROOM-11")]


async def _record_history(app, room_id: str) -> None:
    async with app.state.session_factory() as db:
        agent = Agent(name="historian", engine="echo")
        db.add(agent)
        await db.flush()
        participant = Participant(room_id=room_id, agent_id=agent.id, role="member")
        db.add(participant)
        await db.flush()
        db.add(AgentTurn(
            request_id="turn-history", room_id=room_id,
            target_participant_id=participant.id, agent_id=agent.id,
            idempotency_key="history", state="completed",
        ))
        await db.flush()
        attempt = AgentTurnAttempt(
            turn_id="turn-history", agent_id=agent.id, attempt_number=1,
            generation=0, lease_token="lease-history", state="completed",
        )
        db.add(attempt)
        await db.flush()
        db.add(NativeInvocationAccounting(
            attempt_id=attempt.id, local_execution_id="local-history",
            request_id="turn-history", agent_id=agent.id, room_id=room_id,
            attempt_number=1, generation=0,
        ))
        await db.commit()


async def test_room_with_history_is_retired_and_hidden(room_env) -> None:
    app, room = room_env["app"], room_env["room"]
    headers = {"Authorization": f"Bearer {room_env['token']}"}
    await _record_history(app, room.id)
    async with app.state.session_factory() as db:
        child = Room(project_id=room.project_id, name="child", parent_room_id=room.id)
        db.add(child)
        await db.flush()
        db.add(Participant(room_id=child.id, user_id=room_env["user"].id, role="owner"))
        await db.commit()
        child_id = child.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert len((await client.get("/api/v1/rooms", headers=headers)).json()) == 2

        deleted = await client.delete(f"/api/v1/rooms/{room.id}", headers=headers)
        assert deleted.status_code == 204, deleted.text

        listed = await client.get("/api/v1/rooms", headers=headers)
        assert [row["id"] for row in listed.json()] == [child_id]
        # Direct access stays read-only, as for any archived room.
        read = await client.get(f"/api/v1/rooms/{room.id}", headers=headers)
        assert read.status_code == 200
        assert read.json()["archived_at"] is not None
        write = await client.patch(
            f"/api/v1/rooms/{room.id}", json={"name": "again"}, headers=headers,
        )
        assert write.status_code == 409

    async with app.state.session_factory() as db:
        kept = await db.get(Room, room.id)
        assert kept is not None and kept.archived_by == room_env["user"].id
        assert await db.scalar(select(func.count()).select_from(NativeInvocationAccounting).where(
            NativeInvocationAccounting.room_id == room.id)) == 1
        assert await db.scalar(select(func.count()).select_from(Participant).where(
            Participant.room_id == room.id)) == 2
        # Children move to the top level, as before.
        assert (await db.get(Room, child_id)).parent_room_id is None


async def test_archived_rooms_are_hidden_from_dm_and_sub_room_lists(room_env) -> None:
    app, room = room_env["app"], room_env["room"]
    headers = {"Authorization": f"Bearer {room_env['token']}"}
    async with app.state.session_factory() as db:
        user_id = room_env["user"].id
        dm = Room(project_id=room.project_id, name="dm", is_dm=True)
        sub = Room(project_id=room.project_id, name="sub", parent_room_id=room.id)
        db.add_all([dm, sub])
        await db.flush()
        db.add_all([Participant(room_id=dm.id, user_id=user_id, role="owner"),
                    Participant(room_id=sub.id, user_id=user_id, role="owner")])
        await db.commit()
        dm_id, sub_id = dm.id, sub.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for room_id in (dm_id, sub_id):
            assert (await client.delete(f"/api/v1/rooms/{room_id}", headers=headers)).status_code == 204
        dms = await client.get("/api/v1/rooms", params={"is_dm": "true"}, headers=headers)
        assert dms.json() == []
        subs = await client.get(f"/api/v1/rooms/{room.id}/sub-rooms", headers=headers)
        assert subs.json() == []


class _RoomManager(FakeManager):
    def __init__(self, participant_id: str, generation: int | None) -> None:
        super().__init__(participant_id, generation)
        self.revoked: list[str] = []

    async def revoke_room(self, room_id: str, **_kwargs) -> int:
        self.revoked.append(room_id)
        return 0


async def test_open_turn_is_cancelled_and_agent_told_to_stop(turn_env) -> None:
    request_id, _, _ = await _create_and_deliver(turn_env)
    async with turn_env["factory"]() as db:
        member = await db.get(Participant, turn_env["user_participant"])
        member.role = "owner"
        await db.commit()
    manager = _RoomManager(turn_env["agent_participant"], 3)
    client, headers = await _client(turn_env, manager)
    async with client:
        deleted = await client.delete(f"/api/v1/rooms/{turn_env['room']}", headers=headers["member"])
    assert deleted.status_code == 204, deleted.text

    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        assert turn.state == "cancelled"
        assert (await db.get(Room, turn_env["room"])).archived_at is not None
    assert [frame.request_id for frame in manager.frames if frame.type == "turn_cancel"] == [request_id]
    assert manager.revoked == [turn_env["room"]]


async def test_deleting_a_room_cancels_its_running_execution(harness) -> None:
    flow = await harness.begin_and_delegate()
    execution_id = flow["execution"]["execution_id"]
    worker_turn = await harness.deliver("worker")

    deleted = await harness.client.delete(
        f"/api/v1/rooms/{harness.ids['worker_room']}", headers=harness.user(),
    )
    assert deleted.status_code == 204, deleted.text

    async with harness.sessions() as db:
        execution = await db.get(ProjectExecution, execution_id)
        assert execution.status == "cancelling"
        assert (await db.get(Room, harness.ids["worker_room"])).archived_at is not None
    stop_frames = [f for f in harness.sockets["worker"].frames if f.get("type") == "turn_stop"]
    assert [f["request_id"] for f in stop_frames] == [worker_turn.request_id]


async def test_delete_refused_when_caller_cannot_stop_the_execution(harness, config) -> None:
    flow = await harness.begin_and_delegate()
    execution_id = flow["execution"]["execution_id"]
    await harness.deliver("worker")
    async with harness.sessions() as db:
        status_before = (await db.get(ProjectExecution, execution_id)).status
        sub_owner = User(email="sub-owner@anygarden.io", password_hash="x")
        db.add(sub_owner)
        await db.flush()
        db.add(Participant(room_id=harness.ids["worker_room"], user_id=sub_owner.id, role="owner"))
        await db.commit()
        token = create_user_token(sub_owner.id, sub_owner.email, False, secret=config.jwt_secret)

    deleted = await harness.client.delete(
        f"/api/v1/rooms/{harness.ids['worker_room']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert deleted.status_code == 409, deleted.text
    assert deleted.json()["detail"]["code"] == "ROOM_HAS_ACTIVE_EXECUTION"
    async with harness.sessions() as db:
        assert (await db.get(ProjectExecution, execution_id)).status == status_before
        assert (await db.get(Room, harness.ids["worker_room"])).archived_at is None


async def test_search_skips_archived_rooms(engine, config) -> None:
    from datetime import UTC, datetime

    from anygarden.app import create_app
    from anygarden.db.engine import build_session_factory
    from anygarden.db.models import Message, Project

    sessions = build_session_factory(engine)
    async with sessions() as db:
        user = User(email="searcher@anygarden.io", password_hash="x")
        project = Project(name="p")
        db.add_all([user, project])
        await db.flush()
        live = Room(project_id=project.id, name="live")
        gone = Room(project_id=project.id, name="gone", archived_at=datetime.now(UTC))
        db.add_all([live, gone])
        await db.flush()
        for room in (live, gone):
            part = Participant(room_id=room.id, user_id=user.id, role="owner")
            db.add(part)
            await db.flush()
            db.add(Message(room_id=room.id, participant_id=part.id, content="retireneedle", seq=1))
        await db.commit()
        live_id, user_id = live.id, user.id

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    token = create_user_token(user_id, "searcher@anygarden.io", False, secret=config.jwt_secret)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/search", params={"q": "retireneedle"},
                                headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert {hit["room_id"] for hit in resp.json()} == {live_id}
