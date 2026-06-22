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
from sqlalchemy import func
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentToken,
    Base,
    Message,
    Participant,
    Room,
    Task,
)
from anygarden.mcp.tools import create_task
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.skills_library.service import SkillLibraryService


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


# ── Unit: soft in-flight dedup (#484) ────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_open_task_is_deduplicated(db) -> None:
    """Two create_task calls with the same (room, assignee, title)
    while the first is still open must collapse to a single row. The
    second returns the existing task_id with ``deduplicated=True`` and
    does **not** re-inject the assignment mention (the assignee already
    got woken on the first call)."""
    seeded = await _seed_orchestrator_room(db)
    args = {
        "room_id": seeded["room"].id,
        "title": "design review",
        "assignee_pid": seeded["worker_p"].id,
    }
    first = await create_task(db, agent_id=seeded["orc_agent"].id, arguments=args)
    assert first["isError"] is False
    first_id = first["structuredContent"]["task_id"]
    assert first["structuredContent"].get("deduplicated") in (None, False)

    second = await create_task(db, agent_id=seeded["orc_agent"].id, arguments=args)
    assert second["isError"] is False
    assert second["structuredContent"]["task_id"] == first_id
    assert second["structuredContent"]["deduplicated"] is True

    # Exactly one row, and the mention was injected exactly once.
    rows = (await db.execute(select(Task))).scalars().all()
    assert len(rows) == 1
    msgs = (await db.execute(select(Message))).scalars().all()
    assert len(msgs) == 1


@pytest.mark.asyncio
async def test_dedup_for_unassigned_tasks(db) -> None:
    """The dedup probe treats a NULL assignee consistently — two
    unassigned tasks with the same title collapse to one open row."""
    seeded = await _seed_orchestrator_room(db, second_agent=False)
    args = {"room_id": seeded["room"].id, "title": "later"}
    first = await create_task(db, agent_id=seeded["orc_agent"].id, arguments=args)
    assert first["isError"] is False
    first_id = first["structuredContent"]["task_id"]

    second = await create_task(db, agent_id=seeded["orc_agent"].id, arguments=args)
    assert second["isError"] is False
    assert second["structuredContent"]["task_id"] == first_id
    assert second["structuredContent"]["deduplicated"] is True

    rows = (await db.execute(select(Task))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_closed_duplicate_title_creates_new_task(db) -> None:
    """A finished task with the same title must **not** dedup — the
    probe only matches open ('todo'/'in_progress') rows, so a legit
    repeat ("PR review" again) gets its own fresh row."""
    seeded = await _seed_orchestrator_room(db)
    args = {
        "room_id": seeded["room"].id,
        "title": "PR review",
        "assignee_pid": seeded["worker_p"].id,
    }
    first = await create_task(db, agent_id=seeded["orc_agent"].id, arguments=args)
    first_id = first["structuredContent"]["task_id"]

    # Close the first task — the probe should now miss it.
    done = (
        await db.execute(select(Task).where(Task.id == first_id))
    ).scalar_one()
    done.status = "done"
    await db.flush()

    second = await create_task(db, agent_id=seeded["orc_agent"].id, arguments=args)
    assert second["isError"] is False
    assert second["structuredContent"]["task_id"] != first_id
    assert second["structuredContent"].get("deduplicated") in (None, False)

    rows = (await db.execute(select(Task))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_dedup_scoped_per_assignee(db) -> None:
    """Same title, different assignee → not a duplicate. The dedup key
    includes the assignee participant."""
    seeded = await _seed_orchestrator_room(db)
    # A third agent + participant to be the second distinct assignee.
    other_agent = Agent(name="worker2", engine="echo")
    db.add(other_agent)
    await db.flush()
    other_p = Participant(
        room_id=seeded["room"].id, agent_id=other_agent.id, role="member"
    )
    db.add(other_p)
    await db.flush()

    base = {"room_id": seeded["room"].id, "title": "ship it"}
    r1 = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={**base, "assignee_pid": seeded["worker_p"].id},
    )
    r2 = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={**base, "assignee_pid": other_p.id},
    )
    assert r1["isError"] is False
    assert r2["isError"] is False
    assert r2["structuredContent"]["task_id"] != r1["structuredContent"]["task_id"]
    assert (
        len((await db.execute(select(Task))).scalars().all()) == 2
    )


# ── Unit: fan-out cap (#484) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_fanout_cap_blocks_excess_open_tasks(db, monkeypatch) -> None:
    """Once the room hits its open-task cap, a further create_task is a
    fail-soft tool error (not a crash) and writes no new row."""
    monkeypatch.setenv("ANYGARDEN_MAX_OPEN_TASKS_PER_ROOM", "2")
    seeded = await _seed_orchestrator_room(db, second_agent=False)
    room_id = seeded["room"].id

    # Fill the room to the cap with distinct titles (so dedup never hits).
    for i in range(2):
        r = await create_task(
            db,
            agent_id=seeded["orc_agent"].id,
            arguments={"room_id": room_id, "title": f"task-{i}"},
        )
        assert r["isError"] is False

    # The (cap+1)th distinct open task is refused, fail-soft.
    blocked = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={"room_id": room_id, "title": "overflow"},
    )
    assert blocked["isError"] is True
    assert "cap" in blocked["content"][0]["text"].lower()

    # No overflow row landed.
    total = (
        await db.execute(select(func.count()).select_from(Task))
    ).scalar_one()
    assert total == 2


@pytest.mark.asyncio
async def test_fanout_cap_ignores_closed_tasks(db, monkeypatch) -> None:
    """The cap only counts *open* tasks — closing one frees a slot."""
    monkeypatch.setenv("ANYGARDEN_MAX_OPEN_TASKS_PER_ROOM", "1")
    seeded = await _seed_orchestrator_room(db, second_agent=False)
    room_id = seeded["room"].id

    first = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={"room_id": room_id, "title": "first"},
    )
    assert first["isError"] is False

    # At the cap → the next distinct open task is refused.
    refused = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={"room_id": room_id, "title": "second"},
    )
    assert refused["isError"] is True

    # Close the first → a slot opens up, the next one succeeds.
    done = (
        await db.execute(select(Task).where(Task.id == first["structuredContent"]["task_id"]))
    ).scalar_one()
    done.status = "done"
    await db.flush()

    third = await create_task(
        db,
        agent_id=seeded["orc_agent"].id,
        arguments={"room_id": room_id, "title": "third"},
    )
    assert third["isError"] is False


@pytest.mark.asyncio
async def test_dedup_hit_bypasses_cap(db, monkeypatch) -> None:
    """A dedup hit returns the existing task even when the room is at
    its cap — it creates no new row, so the cap is irrelevant."""
    monkeypatch.setenv("ANYGARDEN_MAX_OPEN_TASKS_PER_ROOM", "1")
    seeded = await _seed_orchestrator_room(db, second_agent=False)
    args = {"room_id": seeded["room"].id, "title": "dup-at-cap"}
    first = await create_task(db, agent_id=seeded["orc_agent"].id, arguments=args)
    assert first["isError"] is False

    # Room is now at the cap (1 open task), but a duplicate dedups.
    second = await create_task(db, agent_id=seeded["orc_agent"].id, arguments=args)
    assert second["isError"] is False
    assert second["structuredContent"]["deduplicated"] is True
    assert second["structuredContent"]["task_id"] == first["structuredContent"]["task_id"]


# ── Integration: JSON-RPC round-trip ────────────────────────────


@pytest_asyncio.fixture()
async def rpc_env():
    """Live FastAPI app + agent token wired so the JSON-RPC endpoint
    accepts ``tools/call create_task``."""
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
