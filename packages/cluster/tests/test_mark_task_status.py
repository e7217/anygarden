"""Tests for the MCP ``mark_task_status`` tool handler (#266).

Validates the contract used by agents to report task progress:
- only the task's current assignee agent may flip the status
- the status value is constrained to a known enum
- the row is updated in place (no new task created on each call)
"""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, AgentToken, Base, Participant, Room, Task, User
from anygarden.mcp.tools import mark_task_status
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.skills_library.service import SkillLibraryService


async def _make_task_assigned_to(db, *, agent_name: str = "bot") -> tuple[Task, Agent, Participant]:
    user = User(email=f"u-{agent_name}@example.com", password_hash="x")
    agent = Agent(name=agent_name, engine="echo")
    db.add_all([user, agent])
    await db.flush()

    room = Room(name=f"r-{agent_name}")
    db.add(room)
    await db.flush()

    p = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add(p)
    await db.flush()

    task = Task(
        room_id=room.id,
        title="t",
        status="todo",
        assignee_participant_id=p.id,
    )
    db.add(task)
    await db.flush()
    return task, agent, p


@pytest.mark.asyncio
async def test_assignee_agent_can_mark_in_progress(db) -> None:
    task, agent, _ = await _make_task_assigned_to(db)
    result = await mark_task_status(
        db, agent_id=agent.id, arguments={"task_id": task.id, "status": "in_progress"}
    )
    assert result["isError"] is False
    await db.refresh(task)
    assert task.status == "in_progress"


@pytest.mark.asyncio
async def test_assignee_agent_can_mark_done(db) -> None:
    task, agent, _ = await _make_task_assigned_to(db)
    result = await mark_task_status(
        db, agent_id=agent.id, arguments={"task_id": task.id, "status": "done"}
    )
    assert result["isError"] is False
    await db.refresh(task)
    assert task.status == "done"


@pytest.mark.asyncio
async def test_assignee_agent_can_mark_failed(db) -> None:
    """#319 — `failed` joined the legal enum so an agent that gives up
    on a task can stamp the same status the goals sweeper would, instead
    of getting a 4xx that forces the workflow into ``blocked``.
    """
    task, agent, _ = await _make_task_assigned_to(db)
    result = await mark_task_status(
        db, agent_id=agent.id, arguments={"task_id": task.id, "status": "failed"}
    )
    assert result["isError"] is False
    await db.refresh(task)
    assert task.status == "failed"


@pytest.mark.asyncio
async def test_non_assignee_is_forbidden(db) -> None:
    task, _, _ = await _make_task_assigned_to(db, agent_name="bot-A")
    intruder = Agent(name="bot-B", engine="echo")
    db.add(intruder)
    await db.flush()

    result = await mark_task_status(
        db,
        agent_id=intruder.id,
        arguments={"task_id": task.id, "status": "done"},
    )
    assert result["isError"] is True
    assert "forbidden" in result["content"][0]["text"].lower()
    await db.refresh(task)
    assert task.status == "todo"


@pytest.mark.asyncio
async def test_unknown_task_is_error(db) -> None:
    agent = Agent(name="lonely", engine="echo")
    db.add(agent)
    await db.flush()

    result = await mark_task_status(
        db,
        agent_id=agent.id,
        arguments={"task_id": "00000000-0000-0000-0000-000000000000", "status": "done"},
    )
    assert result["isError"] is True
    assert "not found" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_invalid_status_is_error(db) -> None:
    task, agent, _ = await _make_task_assigned_to(db)
    result = await mark_task_status(
        db,
        agent_id=agent.id,
        arguments={"task_id": task.id, "status": "exploded"},
    )
    assert result["isError"] is True
    await db.refresh(task)
    assert task.status == "todo"


@pytest.mark.asyncio
async def test_missing_args_is_error(db) -> None:
    agent = Agent(name="x", engine="echo")
    db.add(agent)
    await db.flush()

    result = await mark_task_status(
        db, agent_id=agent.id, arguments={"task_id": "abc"}
    )
    assert result["isError"] is True

    result = await mark_task_status(
        db, agent_id=agent.id, arguments={"status": "done"}
    )
    assert result["isError"] is True


@pytest_asyncio.fixture()
async def mcp_app_env():
    """A live FastAPI app + agent token for end-to-end MCP RPC tests."""
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.machine_bus = bus
    app.state.agent_lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)
    app.state.skill_library_service = SkillLibraryService(factory)

    async with factory() as session:
        agent = Agent(name="bot", engine="echo")
        session.add(agent)
        await session.flush()
        plain = generate_token()
        token_hash, hint = hash_agent_token(plain)
        session.add(
            AgentToken(agent_id=agent.id, token_hash=token_hash, lookup_hint=hint)
        )
        room = Room(name="r")
        session.add(room)
        await session.flush()
        p = Participant(room_id=room.id, agent_id=agent.id, role="member")
        session.add(p)
        await session.flush()
        task = Task(
            room_id=room.id,
            title="t",
            status="todo",
            assignee_participant_id=p.id,
        )
        session.add(task)
        await session.commit()
        agent_id = agent.id
        task_id = task.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "token": plain,
            "agent_id": agent_id,
            "task_id": task_id,
            "factory": factory,
        }
    await engine.dispose()


@pytest.mark.asyncio
async def test_rpc_round_trip_marks_status_and_persists(mcp_app_env) -> None:
    """JSON-RPC ``tools/call mark_task_status`` updates the row and the
    change survives the request boundary (commit happened)."""
    client = mcp_app_env["client"]
    resp = await client.post(
        "/mcp/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "mark_task_status",
                "arguments": {
                    "task_id": mcp_app_env["task_id"],
                    "status": "done",
                },
            },
        },
        headers={"Authorization": f"Bearer {mcp_app_env['token']}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["isError"] is False

    # Re-read in a fresh session to confirm commit.
    async with mcp_app_env["factory"]() as db2:
        task = await db2.get(Task, mcp_app_env["task_id"])
        assert task is not None
        assert task.status == "done"


@pytest.mark.asyncio
async def test_task_without_assignee_is_forbidden(db) -> None:
    """A task that has no assignee at all cannot be marked by anyone —
    there is no agent who could legitimately claim ownership."""
    user = User(email="u@example.com", password_hash="x")
    agent = Agent(name="orphan-bot", engine="echo")
    db.add_all([user, agent])
    await db.flush()
    room = Room(name="r")
    db.add(room)
    await db.flush()
    task = Task(room_id=room.id, title="orphan", status="todo")
    db.add(task)
    await db.flush()

    result = await mark_task_status(
        db, agent_id=agent.id, arguments={"task_id": task.id, "status": "done"}
    )
    assert result["isError"] is True
