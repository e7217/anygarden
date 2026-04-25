"""Tests for the MCP ``create_task`` tool (#270).

The orchestrator agent invokes this to break a user's natural-language
request into N tracked tasks. Phase 1 (#266) gave us the synthetic
mention injection + WS fanout; this tool is the orchestrator-side
entry into that pipeline.

Authorization is the load-bearing piece: the caller must be the room's
designated orchestrator AND the room must run the ``orchestrator``
speaker strategy. Anything else is rejected as a tool-level error
(``isError: true``) so the LLM can recover gracefully.
"""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from doorae.app import create_app
from doorae.auth.token import generate_token, hash_agent_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent,
    AgentToken,
    Base,
    Message,
    Participant,
    Room,
    Task,
)
from doorae.mcp.tools import create_task
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus
from doorae.skills_library.service import SkillLibraryService


async def _seed_orchestrator_room(
    db,
    *,
    strategy: str = "orchestrator",
    second_agent: bool = True,
) -> dict:
    """Build a room with ``speaker_strategy`` set, an orchestrator agent
    (already pinned to the room), and optionally a worker agent that
    can receive task assignments. Returns ids the tests need."""
    orc_agent = Agent(name="orc", engine="echo")
    db.add(orc_agent)
    await db.flush()

    worker_agent = None
    if second_agent:
        worker_agent = Agent(name="worker", engine="echo")
        db.add(worker_agent)
        await db.flush()

    room = Room(
        name="r",
        speaker_strategy=strategy,
        orchestrator_agent_id=orc_agent.id,
    )
    db.add(room)
    await db.flush()

    orc_p = Participant(
        room_id=room.id, agent_id=orc_agent.id, role="member"
    )
    db.add(orc_p)
    worker_p = None
    if worker_agent is not None:
        worker_p = Participant(
            room_id=room.id, agent_id=worker_agent.id, role="member"
        )
        db.add(worker_p)
    await db.flush()
    return {
        "orc_agent": orc_agent,
        "worker_agent": worker_agent,
        "room": room,
        "orc_p": orc_p,
        "worker_p": worker_p,
    }


# ── Unit: handler called directly with a session ─────────────────


@pytest.mark.asyncio
async def test_orchestrator_can_create_and_assign_task(db) -> None:
    seeded = await _seed_orchestrator_room(db)
    result = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={
            "room_id": seeded["room"].id,
            "title": "design review",
            "assignee_pid": seeded["worker_p"].id,
        },
    )
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert "task_id" in structured
    assert structured["assignee_pid"] == seeded["worker_p"].id

    # The row landed.
    task = (await db.execute(select(Task))).scalar_one()
    assert task.title == "design review"
    assert task.assignee_participant_id == seeded["worker_p"].id
    assert task.status == "todo"
    assert task.created_by is None  # agent-created, not user-created

    # And the synthetic mention message was injected (Phase 1 path).
    msgs = (await db.execute(select(Message))).scalars().all()
    assert len(msgs) == 1
    meta = msgs[0].extra_metadata
    assert meta["mentions"][0]["id"] == seeded["worker_p"].id
    assert meta["task_assignment"]["task_id"] == task.id


@pytest.mark.asyncio
async def test_create_without_assignee_skips_injection(db) -> None:
    """A task with no assignee is fine — it just doesn't auto-trigger
    anyone. Useful for orchestrators that capture intent but defer
    delegation."""
    seeded = await _seed_orchestrator_room(db, second_agent=False)
    result = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={
            "room_id": seeded["room"].id,
            "title": "later",
        },
    )
    assert result["isError"] is False
    msgs = (await db.execute(select(Message))).scalars().all()
    assert msgs == []


@pytest.mark.asyncio
async def test_non_orchestrator_agent_is_forbidden(db) -> None:
    seeded = await _seed_orchestrator_room(db)
    intruder = Agent(name="other", engine="echo")
    db.add(intruder)
    await db.flush()
    intruder_p = Participant(
        room_id=seeded["room"].id, agent_id=intruder.id, role="member"
    )
    db.add(intruder_p)
    await db.flush()

    result = await create_task(
        db,
        agent_id=intruder.id,
        arguments={
            "room_id": seeded["room"].id,
            "title": "x",
            "assignee_pid": seeded["worker_p"].id,
        },
    )
    assert result["isError"] is True
    assert "orchestrator" in result["content"][0]["text"].lower()
    # And nothing was written.
    assert (await db.execute(select(Task))).scalars().all() == []


@pytest.mark.asyncio
async def test_room_without_orchestrator_strategy_is_forbidden(db) -> None:
    """Even the room's pinned orchestrator may not call ``create_task``
    when the room is in ``mentioned_only`` mode — the implicit contract
    is that orchestration is the active strategy."""
    seeded = await _seed_orchestrator_room(db, strategy="mentioned_only")
    result = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={
            "room_id": seeded["room"].id,
            "title": "x",
        },
    )
    assert result["isError"] is True
    assert "strategy" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_self_loop_assignee_is_rejected(db) -> None:
    """Assigning a task to the orchestrator's own participant would
    feed its turn back into itself indefinitely — defended at the
    handler boundary (plan §6 R2)."""
    seeded = await _seed_orchestrator_room(db)
    result = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={
            "room_id": seeded["room"].id,
            "title": "self-task",
            "assignee_pid": seeded["orc_p"].id,
        },
    )
    assert result["isError"] is True
    assert "self" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_assignee_from_different_room_is_rejected(db) -> None:
    seeded = await _seed_orchestrator_room(db)
    other = Room(name="other")
    db.add(other)
    await db.flush()
    outside_agent = Agent(name="outsider", engine="echo")
    db.add(outside_agent)
    await db.flush()
    outside_p = Participant(
        room_id=other.id, agent_id=outside_agent.id, role="member"
    )
    db.add(outside_p)
    await db.flush()

    result = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={
            "room_id": seeded["room"].id,
            "title": "x",
            "assignee_pid": outside_p.id,
        },
    )
    assert result["isError"] is True
    assert "participant" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_missing_required_args(db) -> None:
    seeded = await _seed_orchestrator_room(db, second_agent=False)
    # missing title
    r1 = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={"room_id": seeded["room"].id},
    )
    assert r1["isError"] is True
    # missing room_id
    r2 = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={"title": "x"},
    )
    assert r2["isError"] is True


@pytest.mark.asyncio
async def test_unknown_room_is_error(db) -> None:
    agent = Agent(name="lonely", engine="echo")
    db.add(agent)
    await db.flush()
    result = await create_task(
        db,
        agent_id=agent.id,
        arguments={
            "room_id": "00000000-0000-0000-0000-000000000000",
            "title": "x",
        },
    )
    assert result["isError"] is True


# ── Integration: JSON-RPC round-trip ────────────────────────────


@pytest_asyncio.fixture()
async def rpc_env():
    """Live FastAPI app + agent token wired so the JSON-RPC endpoint
    accepts ``tools/call create_task``."""
    config = DooraeSettings(
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
        seeded = await _seed_orchestrator_room(session)
        plain = generate_token()
        token_hash, hint = hash_agent_token(plain)
        session.add(
            AgentToken(
                agent_id=seeded["orc_agent"].id,
                token_hash=token_hash,
                lookup_hint=hint,
            )
        )
        await session.commit()
        room_id = seeded["room"].id
        worker_pid = seeded["worker_p"].id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "token": plain,
            "factory": factory,
            "room_id": room_id,
            "worker_pid": worker_pid,
        }
    await engine.dispose()


@pytest.mark.asyncio
async def test_rpc_round_trip_creates_task_and_persists(rpc_env) -> None:
    resp = await rpc_env["client"].post(
        "/mcp/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "create_task",
                "arguments": {
                    "room_id": rpc_env["room_id"],
                    "title": "from-rpc",
                    "assignee_pid": rpc_env["worker_pid"],
                },
            },
        },
        headers={"Authorization": f"Bearer {rpc_env['token']}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["isError"] is False

    # Survives the request boundary.
    async with rpc_env["factory"]() as db2:
        rows = (await db2.execute(select(Task))).scalars().all()
        assert len(rows) == 1
        assert rows[0].title == "from-rpc"


@pytest.mark.asyncio
async def test_rpc_lists_create_task_in_tools(rpc_env) -> None:
    resp = await rpc_env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={"Authorization": f"Bearer {rpc_env['token']}"},
    )
    body = resp.json()
    names = {t["name"] for t in body["result"]["tools"]}
    assert "create_task" in names
