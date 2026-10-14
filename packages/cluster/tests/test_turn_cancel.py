"""ENG-09 — a room member can cancel an open agent turn in a general room."""

from __future__ import annotations

import secrets

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.models import (
    ActivityLog,
    AgentTurn,
    AgentTurnAttempt,
    AgentTurnOutbox,
    Participant,
    ProjectExecution,
    Room,
    User,
)
from anygarden.db.repository import append_message
from anygarden.turns.service import create_turn

from .test_durable_turns import FakeManager, _create_and_deliver
from .test_durable_turns import turn_env as turn_env

pytestmark = [pytest.mark.asyncio, pytest.mark.req("ENG-09")]


async def _client(env, manager=None):
    jwt_secret = secrets.token_urlsafe(32)
    async with env["factory"]() as db:
        member = await db.scalar(select(User).where(User.email == "turns@test.com"))
        outsider = User(email="outsider@test.com", password_hash="x")
        observer = User(email="observer@test.com", password_hash="x")
        db.add_all([outsider, observer])
        await db.flush()
        db.add(Participant(room_id=env["room"], user_id=observer.id, role="observer"))
        await db.commit()
        tokens = {
            name: create_user_token(user.id, user.email, False, secret=jwt_secret)
            for name, user in (("member", member), ("outsider", outsider), ("observer", observer))
        }
    app = create_app(AnygardenSettings(
        db_url="sqlite+aiosqlite://", jwt_secret=jwt_secret, log_level="WARNING",
    ))
    app.state.engine = env["engine"]
    app.state.session_factory = env["factory"]
    app.state.connection_manager = manager
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, {k: {"Authorization": f"Bearer {v}"} for k, v in tokens.items()}


async def _create_pending(env) -> str:
    async with env["factory"]() as db:
        trigger = await append_message(db, env["room"], env["user_participant"], "hello")
        turn = await create_turn(
            db, room_id=env["room"], participant_id=env["agent_participant"],
            agent_id=env["agent"], trigger_message_id=trigger.id,
        )
        request_id = turn.request_id
        await db.commit()
    return request_id


async def test_member_cancels_running_turn_and_agent_is_told_to_stop(turn_env) -> None:
    request_id, _, delivery = await _create_and_deliver(turn_env)
    manager = FakeManager(turn_env["agent_participant"], 3)
    client, headers = await _client(turn_env, manager)
    async with client:
        listed = await client.get(
            "/api/v1/turns/open", params={"room_id": turn_env["room"]}, headers=headers["member"],
        )
        assert listed.status_code == 200
        assert [row["request_id"] for row in listed.json()] == [request_id]
        assert listed.json()[0]["participant_id"] == turn_env["agent_participant"]
        assert listed.json()[0]["thread_root_id"] is None

        res = await client.post(f"/api/v1/turns/{request_id}/cancel", headers=headers["member"])
        assert res.status_code == 200, res.text
        assert res.json()["state"] == "cancelled"
        assert res.json()["changed"] is True
        assert res.json()["stop_sent"] is True

        listed = await client.get(
            "/api/v1/turns/open", params={"room_id": turn_env["room"]}, headers=headers["member"],
        )
        assert listed.json() == []

    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        attempt = await db.scalar(select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id))
        outbox = await db.scalar(select(AgentTurnOutbox).where(AgentTurnOutbox.turn_id == request_id))
        log = await db.scalar(select(ActivityLog).where(
            ActivityLog.request_id == request_id, ActivityLog.event_type == "turn_cancelled",
        ))
        assert turn.state == "cancelled" and turn.terminal_reason == "user_cancelled"
        assert turn.completed_at is not None
        assert attempt.state == "cancelled" and attempt.outcome == "cancelled"
        assert outbox.state in {"delivered", "cancelled"}
        assert log is not None

    stops = [f for f in manager.frames if getattr(f, "type", None) == "turn_cancel"]
    assert len(stops) == 1
    metadata = delivery.frames[0].metadata
    assert stops[0].request_id == request_id
    assert stops[0].room_id == turn_env["room"]
    assert stops[0].agent_id == turn_env["agent"]
    assert stops[0].attempt == metadata["turn_attempt"]
    assert stops[0].generation == metadata["turn_generation"]
    assert stops[0].reason == "user_cancelled"
    typing = [f for _, f in manager.broadcasts if getattr(f, "type", None) == "typing"]
    assert typing and typing[-1].is_typing is False
    assert typing[-1].participant_id == turn_env["agent_participant"]


async def test_cancel_pending_turn_cancels_outbox_without_stop_frame(turn_env) -> None:
    request_id = await _create_pending(turn_env)
    manager = FakeManager(turn_env["agent_participant"], 3)
    client, headers = await _client(turn_env, manager)
    async with client:
        res = await client.post(f"/api/v1/turns/{request_id}/cancel", headers=headers["member"])
        assert res.status_code == 200, res.text
        assert res.json()["stop_sent"] is False
    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        outbox = await db.scalar(select(AgentTurnOutbox).where(AgentTurnOutbox.turn_id == request_id))
        attempt = await db.scalar(select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id))
        assert turn.state == "cancelled"
        assert outbox.state == "cancelled"
        assert attempt.state == "cancelled"
    assert not [f for f in manager.frames if getattr(f, "type", None) == "turn_cancel"]


async def test_open_turns_report_the_thread_they_answer_in(turn_env) -> None:
    async with turn_env["factory"]() as db:
        root = await append_message(db, turn_env["room"], turn_env["user_participant"], "root")
        turn = await create_turn(
            db, room_id=turn_env["room"], participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"], trigger_message_id=root.id, thread_root_id=root.id,
        )
        request_id, root_id = turn.request_id, root.id
        await db.commit()
    client, headers = await _client(turn_env, None)
    async with client:
        listed = await client.get(
            "/api/v1/turns/open", params={"room_id": turn_env["room"]}, headers=headers["member"],
        )
    assert listed.status_code == 200
    assert [(row["request_id"], row["thread_root_id"]) for row in listed.json()] == [(request_id, root_id)]


async def test_cancel_is_idempotent_and_terminal_turns_conflict(turn_env) -> None:
    request_id = await _create_pending(turn_env)
    completed_id = await _create_pending(turn_env)
    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, completed_id)
        turn.state = "completed"
        await db.commit()
    client, headers = await _client(turn_env, FakeManager(turn_env["agent_participant"], 3))
    async with client:
        first = await client.post(f"/api/v1/turns/{request_id}/cancel", headers=headers["member"])
        second = await client.post(f"/api/v1/turns/{request_id}/cancel", headers=headers["member"])
        assert first.status_code == second.status_code == 200
        assert second.json()["changed"] is False
        assert second.json()["state"] == "cancelled"
        done = await client.post(f"/api/v1/turns/{completed_id}/cancel", headers=headers["member"])
        assert done.status_code == 409
        assert done.json()["detail"]["code"] == "TURN_ALREADY_TERMINAL"
        missing = await client.post("/api/v1/turns/nope/cancel", headers=headers["member"])
        assert missing.status_code == 404


async def test_cancel_requires_message_send_in_the_turns_room(turn_env) -> None:
    request_id = await _create_pending(turn_env)
    client, headers = await _client(turn_env, None)
    async with client:
        outsider = await client.post(f"/api/v1/turns/{request_id}/cancel", headers=headers["outsider"])
        observer = await client.post(f"/api/v1/turns/{request_id}/cancel", headers=headers["observer"])
        assert outsider.status_code in {403, 404}
        assert observer.status_code == 403
        listed = await client.get(
            "/api/v1/turns/open", params={"room_id": turn_env["room"]}, headers=headers["outsider"],
        )
        assert listed.status_code in {403, 404}
    async with turn_env["factory"]() as db:
        assert (await db.get(AgentTurn, request_id)).state == "pending"


async def test_execution_bound_turn_uses_execution_cancel_path(turn_env) -> None:
    request_id = await _create_pending(turn_env)
    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        room = await db.get(Room, turn_env["room"])
        execution = ProjectExecution(
            project_id=room.project_id, operating_room_id=room.id,
            lead_agent_id=turn_env["agent"], source_message_id=turn.trigger_message_id,
            objective="ship it",
        )
        db.add(execution)
        await db.flush()
        turn.execution_id = execution.id
        turn.execution_input_revision = 1
        await db.commit()
    client, headers = await _client(turn_env, None)
    async with client:
        res = await client.post(f"/api/v1/turns/{request_id}/cancel", headers=headers["member"])
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "TURN_BOUND_TO_EXECUTION"
        listed = await client.get(
            "/api/v1/turns/open", params={"room_id": turn_env["room"]}, headers=headers["member"],
        )
        assert listed.json() == []
    async with turn_env["factory"]() as db:
        assert (await db.get(AgentTurn, request_id)).state == "pending"
