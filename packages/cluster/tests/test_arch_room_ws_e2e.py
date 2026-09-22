"""Loopback TCP WS: human send -> durable lease -> room fake CLI -> ledger."""

import asyncio
import importlib.util
import json
import os
import secrets as entropy
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn
import websockets
from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.budgets.ledger import compute_observed_tokens
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    ActivityLog,
    Agent,
    AgentToken,
    AgentTurn,
    AgentTurnAttempt,
    Base,
    Participant,
    Project,
    Room,
    UsageLedger,
    User,
)
from anygarden_agent.cli import _setup_engine
from anygarden_agent.client import ChatClient
from anygarden_agent.runtime.execution.launch import ExecutionLaunch
from sqlalchemy import select

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX subprocess runtime")
_fixture_path = (
    Path(__file__).resolve().parents[2] / "agent/tests/test_room_execution.py"
)
_spec = importlib.util.spec_from_file_location("_ws_room_fixture", _fixture_path)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
setup_room = _fixture.setup_room


@pytest.mark.parametrize("engine_name", ["codex-cli", "pi-cli"])
@pytest.mark.parametrize("outcome", ["ok", "failed", "timeout", "cancel"])
async def test_real_ws_lease_to_usage(setup_room, monkeypatch, engine_name, outcome):
    root = setup_room
    monkeypatch.setenv("TEST_ENGINE", engine_name)
    monkeypatch.setenv("TEST_OUTCOME", outcome)
    if outcome == "timeout":
        monkeypatch.setenv("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC", "0.5")
    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{root}/cluster.db",
        jwt_secret=entropy.token_urlsafe(32),
        log_level="WARNING",
    )
    db_engine = build_engine(config.db_url)
    factory = build_session_factory(db_engine)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user = User(
            email="ws-local@test.invalid", password_hash="fixture", is_admin=True
        )
        project = Project(name="ws-local")
        db.add_all([user, project])
        await db.flush()
        room = Room(project_id=project.id, name="ws-local")
        agent = Agent(
            name="agent",
            engine=engine_name,
            provider="local",
            model="model",
            generation=7,
            desired_state="running",
            actual_state="running",
        )
        db.add_all([room, agent])
        await db.flush()
        db.add_all(
            [
                Participant(room_id=room.id, user_id=user.id, role="admin"),
                Participant(room_id=room.id, agent_id=agent.id, role="member"),
            ]
        )
        token = generate_token()
        hashed, hint = hash_agent_token(token)
        db.add(AgentToken(agent_id=agent.id, token_hash=hashed, lookup_hint=hint))
        await db.commit()
        room_id = room.id
        agent_id = agent.id
        jwt = create_user_token(user.id, user.email, True, secret=config.jwt_secret)
    app = create_app(config)
    app.state.engine = db_engine
    app.state.session_factory = factory
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    client = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
        client = ChatClient(
            f"ws://127.0.0.1:{port}",
            token=token,
            agent_name="agent",
            execution_launch=ExecutionLaunch(engine_name, "local", "model", 7, None),
        )
        await _setup_engine(client, engine_name, "agent", "model", None)
        seen = asyncio.Event()
        runtime = client._execution_adapter._manager._runtime
        original = runtime._collect

        async def collect(proc, inv, session, output, emit, authorized):
            def observe(kind, event):
                emit(kind, event)
                if kind == "progress" and event.get("event") in {
                    "turn_end",
                    "item.started",
                }:
                    seen.set()

            return await original(proc, inv, session, output, observe, authorized)

        monkeypatch.setattr(runtime, "_collect", collect)
        await client.join_room(room_id)
        async with asyncio.timeout(10):
            while client._agent_id != agent_id:
                await asyncio.sleep(0.01)
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ws/rooms/{room_id}",
            subprotocols=["anygarden.v1", "bearer." + jwt],
        ) as human:
            assert json.loads(await human.recv())["type"] == "welcome"
            await human.send(json.dumps({"type": "send", "content": "@agent hello"}))
            await asyncio.wait_for(seen.wait(), 10)
            if outcome == "cancel":
                manager = client._execution_adapter._manager
                assert len(manager._tasks) == 1
                await manager.cancel(next(iter(manager._tasks)))
            async with asyncio.timeout(10):
                while True:
                    async with factory() as db:
                        rows = (
                            (
                                await db.execute(
                                    select(UsageLedger).where(
                                        UsageLedger.agent_id == agent_id
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        ends = (
                            (
                                await db.execute(
                                    select(ActivityLog).where(
                                        ActivityLog.agent_id == agent_id,
                                        ActivityLog.event_type == "handler_finished",
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        if rows and ends:
                            break
                    await asyncio.sleep(0.01)
            async with factory() as db:
                rows = (
                    (
                        await db.execute(
                            select(UsageLedger).where(UsageLedger.agent_id == agent_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(rows) == 1 and (
                    rows[0].prompt_tokens,
                    rows[0].completion_tokens,
                ) == (3, 1)
                assert (
                    await compute_observed_tokens(
                        db,
                        scope_type="agent",
                        scope_id=agent_id,
                        window_start=datetime.now(timezone.utc) - timedelta(hours=1),
                    )
                    == 4
                )
                turn = (
                    (
                        await db.execute(
                            select(AgentTurn).where(AgentTurn.agent_id == agent_id)
                        )
                    )
                    .scalars()
                    .one()
                )
                attempt = (
                    (
                        await db.execute(
                            select(AgentTurnAttempt).where(
                                AgentTurnAttempt.turn_id == turn.request_id
                            )
                        )
                    )
                    .scalars()
                    .one()
                )
                assert (
                    turn.protocol_version != 0
                    and attempt.generation == 7
                    and attempt.lease_token
                )
                assert attempt.started_at is not None
                if outcome == "cancel":
                    assert turn.state == "cancelled"
                    assert turn.completed_at is not None
                    assert attempt.state == "cancelled"
                    assert attempt.ended_at is not None
                assert ends[0].outcome == (
                    "cancelled" if outcome == "cancel" else outcome
                )
                request_id = turn.request_id
                attempt_no = attempt.attempt_number
                lease = attempt.lease_token
            if outcome == "cancel":
                connection = client._connections[room_id]
                client._execution_adapter._environment["TEST_OUTCOME"] = "ok"
                await human.send(
                    json.dumps({"type": "send", "content": "@agent next turn"})
                )
                async with asyncio.timeout(10):
                    while True:
                        async with factory() as db:
                            turns = (
                                (
                                    await db.execute(
                                        select(AgentTurn).where(
                                            AgentTurn.agent_id == agent_id
                                        )
                                    )
                                )
                                .scalars()
                                .all()
                            )
                            rows = (
                                (
                                    await db.execute(
                                        select(UsageLedger).where(
                                            UsageLedger.agent_id == agent_id
                                        )
                                    )
                                )
                                .scalars()
                                .all()
                            )
                            if (
                                len(turns) == 2
                                and len(rows) == 2
                                and any(t.state == "completed" for t in turns)
                            ):
                                break
                        await asyncio.sleep(0.01)
                assert client._connections[room_id] is connection
                assert sorted(t.state for t in turns) == ["cancelled", "completed"]
                assert all(
                    (r.prompt_tokens, r.completion_tokens) == (3, 1) for r in rows
                )
            # Authenticated but wrong lease cannot inject usage into this turn.
            async with websockets.connect(
                f"ws://127.0.0.1:{port}/ws/rooms/{room_id}?generation=7",
                subprotocols=["anygarden.v1", "bearer." + token],
            ) as replay:
                assert json.loads(await replay.recv())["type"] == "welcome"
                await replay.send(
                    json.dumps(
                        dict(
                            type="lifecycle",
                            room_id=room_id,
                            request_id=request_id,
                            event="engine_call_finished",
                            outcome="ok",
                            engine=engine_name,
                            model="model",
                            input_tokens=900,
                            output_tokens=100,
                            turn_attempt=attempt_no,
                            turn_generation=7,
                            turn_lease="wrong-" + lease,
                        )
                    )
                )
                async with asyncio.timeout(5):
                    while True:
                        async with factory() as db:
                            logs = (
                                (
                                    await db.execute(
                                        select(ActivityLog).where(
                                            ActivityLog.agent_id == agent_id
                                        )
                                    )
                                )
                                .scalars()
                                .all()
                            )
                            if any(
                                "lease_mismatch" in str(log.details) for log in logs
                            ):
                                break
                        await asyncio.sleep(0.01)
                async with factory() as db:
                    assert len(
                        (
                            await db.execute(
                                select(UsageLedger).where(
                                    UsageLedger.agent_id == agent_id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    ) == (2 if outcome == "cancel" else 1)
    finally:
        if client is not None:
            await client.close()
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        sock.close()
        await db_engine.dispose()
