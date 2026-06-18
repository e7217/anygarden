"""Tests for the task_blockers dependency relation (#459, Wave 2c).

Covers the two MCP tools and the resolve-wake hook:

- ``add_task_blocker``  — assignee-only; rejects self-reference and cycles;
  idempotent insert.
- ``clear_task_blocker`` — assignee-only; removes the edge.
- resolve-wake — when a blocker reaches a terminal status the dependent is
  returned to ``todo`` + re-woken via ``inject_task_assignment_message``,
  but ONLY once *every* blocker of the dependent is terminal.
"""

from __future__ import annotations

import secrets

import pytest
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
    Participant,
    Room,
    Task,
    TaskBlocker,
)
from anygarden.mcp.tools import (
    add_task_blocker,
    clear_task_blocker,
    mark_task_status,
)
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.skills_library.service import SkillLibraryService


async def _agent_with_task(
    db,
    *,
    agent_name: str,
    room: Room,
    status: str = "todo",
    title: str = "t",
) -> tuple[Task, Agent, Participant]:
    """Create an agent, a participant in *room*, and a task assigned to it."""
    agent = Agent(name=agent_name, engine="echo")
    db.add(agent)
    await db.flush()
    p = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add(p)
    await db.flush()
    task = Task(
        room_id=room.id,
        title=title,
        status=status,
        assignee_participant_id=p.id,
    )
    db.add(task)
    await db.flush()
    return task, agent, p


async def _room(db, name: str = "r") -> Room:
    room = Room(name=name)
    db.add(room)
    await db.flush()
    return room


async def _blocker_count(db, *, task_id: str) -> int:
    rows = (
        await db.execute(
            select(TaskBlocker).where(TaskBlocker.task_id == task_id)
        )
    ).scalars().all()
    return len(rows)


# ── add_task_blocker ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_task_blocker_inserts(db) -> None:
    room = await _room(db)
    dep, agent, _ = await _agent_with_task(db, agent_name="bot", room=room)
    blocker, _, _ = await _agent_with_task(db, agent_name="bot2", room=room)

    result = await add_task_blocker(
        db,
        agent_id=agent.id,
        arguments={"task_id": dep.id, "blocked_by_task_id": blocker.id},
    )
    assert result["isError"] is False
    assert await _blocker_count(db, task_id=dep.id) == 1


@pytest.mark.asyncio
async def test_add_task_blocker_is_idempotent(db) -> None:
    room = await _room(db)
    dep, agent, _ = await _agent_with_task(db, agent_name="bot", room=room)
    blocker, _, _ = await _agent_with_task(db, agent_name="bot2", room=room)
    args = {"task_id": dep.id, "blocked_by_task_id": blocker.id}

    assert (await add_task_blocker(db, agent_id=agent.id, arguments=args))[
        "isError"
    ] is False
    # Re-adding the same edge succeeds without creating a second row.
    assert (await add_task_blocker(db, agent_id=agent.id, arguments=args))[
        "isError"
    ] is False
    assert await _blocker_count(db, task_id=dep.id) == 1


@pytest.mark.asyncio
async def test_add_task_blocker_non_assignee_forbidden(db) -> None:
    room = await _room(db)
    dep, _, _ = await _agent_with_task(db, agent_name="owner", room=room)
    blocker, _, _ = await _agent_with_task(db, agent_name="b", room=room)
    intruder = Agent(name="intruder", engine="echo")
    db.add(intruder)
    await db.flush()

    result = await add_task_blocker(
        db,
        agent_id=intruder.id,
        arguments={"task_id": dep.id, "blocked_by_task_id": blocker.id},
    )
    assert result["isError"] is True
    assert "forbidden" in result["content"][0]["text"].lower()
    assert await _blocker_count(db, task_id=dep.id) == 0


@pytest.mark.asyncio
async def test_add_task_blocker_self_reference_rejected(db) -> None:
    room = await _room(db)
    dep, agent, _ = await _agent_with_task(db, agent_name="bot", room=room)

    result = await add_task_blocker(
        db,
        agent_id=agent.id,
        arguments={"task_id": dep.id, "blocked_by_task_id": dep.id},
    )
    assert result["isError"] is True
    assert "itself" in result["content"][0]["text"].lower()
    assert await _blocker_count(db, task_id=dep.id) == 0


@pytest.mark.asyncio
async def test_add_task_blocker_cycle_rejected(db) -> None:
    """A blocks B (B blocked_by A). Then trying A blocked_by B closes a
    cycle and must be rejected by the transitive guard."""
    room = await _room(db)
    a, agent_a, _ = await _agent_with_task(db, agent_name="a", room=room)
    b, agent_b, _ = await _agent_with_task(db, agent_name="b", room=room)

    # B is blocked by A (edge B -> A).
    r1 = await add_task_blocker(
        db,
        agent_id=agent_b.id,
        arguments={"task_id": b.id, "blocked_by_task_id": a.id},
    )
    assert r1["isError"] is False

    # Now A blocked by B (edge A -> B) would close A -> B -> A. Reject.
    r2 = await add_task_blocker(
        db,
        agent_id=agent_a.id,
        arguments={"task_id": a.id, "blocked_by_task_id": b.id},
    )
    assert r2["isError"] is True
    assert "cycle" in r2["content"][0]["text"].lower()
    assert await _blocker_count(db, task_id=a.id) == 0


@pytest.mark.asyncio
async def test_add_task_blocker_unknown_blocker_is_error(db) -> None:
    room = await _room(db)
    dep, agent, _ = await _agent_with_task(db, agent_name="bot", room=room)

    result = await add_task_blocker(
        db,
        agent_id=agent.id,
        arguments={
            "task_id": dep.id,
            "blocked_by_task_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert result["isError"] is True
    assert "not found" in result["content"][0]["text"].lower()


# ── clear_task_blocker ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_task_blocker_removes(db) -> None:
    room = await _room(db)
    dep, agent, _ = await _agent_with_task(db, agent_name="bot", room=room)
    blocker, _, _ = await _agent_with_task(db, agent_name="bot2", room=room)
    args = {"task_id": dep.id, "blocked_by_task_id": blocker.id}

    await add_task_blocker(db, agent_id=agent.id, arguments=args)
    assert await _blocker_count(db, task_id=dep.id) == 1

    result = await clear_task_blocker(db, agent_id=agent.id, arguments=args)
    assert result["isError"] is False
    assert await _blocker_count(db, task_id=dep.id) == 0


@pytest.mark.asyncio
async def test_clear_task_blocker_non_assignee_forbidden(db) -> None:
    room = await _room(db)
    dep, agent, _ = await _agent_with_task(db, agent_name="owner", room=room)
    blocker, _, _ = await _agent_with_task(db, agent_name="b", room=room)
    await add_task_blocker(
        db,
        agent_id=agent.id,
        arguments={"task_id": dep.id, "blocked_by_task_id": blocker.id},
    )
    intruder = Agent(name="intruder", engine="echo")
    db.add(intruder)
    await db.flush()

    result = await clear_task_blocker(
        db,
        agent_id=intruder.id,
        arguments={"task_id": dep.id, "blocked_by_task_id": blocker.id},
    )
    assert result["isError"] is True
    assert "forbidden" in result["content"][0]["text"].lower()
    # The edge survives the rejected clear.
    assert await _blocker_count(db, task_id=dep.id) == 1


# ── resolve-wake ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_wake_single_blocker(db, monkeypatch) -> None:
    """B blocked by A. Marking A done returns B to todo and re-wakes it."""
    room = await _room(db)
    a, agent_a, _ = await _agent_with_task(db, agent_name="a", room=room)
    b, agent_b, _ = await _agent_with_task(
        db, agent_name="b", room=room, status="blocked"
    )
    await add_task_blocker(
        db,
        agent_id=agent_b.id,
        arguments={"task_id": b.id, "blocked_by_task_id": a.id},
    )

    injected: list[str] = []

    async def _capture(db_, *, room, task, sender_participant_id, event="assigned", manager=None):  # noqa: E501
        injected.append(task.id)

        class _Msg:
            pass

        return _Msg()

    monkeypatch.setattr(
        "anygarden.messages.service.inject_task_assignment_message", _capture
    )

    # A finishes.
    result = await mark_task_status(
        db, agent_id=agent_a.id, arguments={"task_id": a.id, "status": "done"}
    )
    assert result["isError"] is False
    assert result["structuredContent"]["woken"] == [b.id]

    await db.refresh(b)
    assert b.status == "todo"
    assert b.assigned_at is not None
    assert injected == [b.id]
    # The satisfied edge was removed.
    assert await _blocker_count(db, task_id=b.id) == 0


@pytest.mark.asyncio
async def test_resolve_wake_only_when_all_blockers_cleared(db, monkeypatch) -> None:
    """B blocked by both A and C. Marking A done must NOT wake B (C is
    still pending). Then marking C done wakes B."""
    room = await _room(db)
    a, agent_a, _ = await _agent_with_task(db, agent_name="a", room=room)
    c, agent_c, _ = await _agent_with_task(db, agent_name="c", room=room)
    b, agent_b, _ = await _agent_with_task(
        db, agent_name="b", room=room, status="blocked"
    )
    await add_task_blocker(
        db,
        agent_id=agent_b.id,
        arguments={"task_id": b.id, "blocked_by_task_id": a.id},
    )
    await add_task_blocker(
        db,
        agent_id=agent_b.id,
        arguments={"task_id": b.id, "blocked_by_task_id": c.id},
    )

    injected: list[str] = []

    async def _capture(db_, *, room, task, sender_participant_id, event="assigned", manager=None):  # noqa: E501
        injected.append(task.id)

        class _Msg:
            pass

        return _Msg()

    monkeypatch.setattr(
        "anygarden.messages.service.inject_task_assignment_message", _capture
    )

    # A done — C still pending, so B stays blocked.
    r1 = await mark_task_status(
        db, agent_id=agent_a.id, arguments={"task_id": a.id, "status": "done"}
    )
    assert r1["structuredContent"]["woken"] == []
    await db.refresh(b)
    assert b.status == "blocked"
    assert injected == []
    # The A edge was cleared, but the C edge remains.
    assert await _blocker_count(db, task_id=b.id) == 1

    # C done — now all blockers terminal, B wakes.
    r2 = await mark_task_status(
        db, agent_id=agent_c.id, arguments={"task_id": c.id, "status": "done"}
    )
    assert r2["structuredContent"]["woken"] == [b.id]
    await db.refresh(b)
    assert b.status == "todo"
    assert injected == [b.id]
    assert await _blocker_count(db, task_id=b.id) == 0


@pytest.mark.asyncio
async def test_resolve_wake_failed_blocker_also_unblocks(db, monkeypatch) -> None:
    """A blocker that ``failed`` (not just ``done``) is still terminal and
    unblocks its dependent."""
    room = await _room(db)
    a, agent_a, _ = await _agent_with_task(db, agent_name="a", room=room)
    b, agent_b, _ = await _agent_with_task(
        db, agent_name="b", room=room, status="blocked"
    )
    await add_task_blocker(
        db,
        agent_id=agent_b.id,
        arguments={"task_id": b.id, "blocked_by_task_id": a.id},
    )

    injected: list[str] = []

    async def _capture(db_, *, room, task, sender_participant_id, event="assigned", manager=None):  # noqa: E501
        injected.append(task.id)
        return object()

    monkeypatch.setattr(
        "anygarden.messages.service.inject_task_assignment_message", _capture
    )

    await mark_task_status(
        db, agent_id=agent_a.id, arguments={"task_id": a.id, "status": "failed"}
    )
    await db.refresh(b)
    assert b.status == "todo"
    assert injected == [b.id]


@pytest.mark.asyncio
async def test_resolve_wake_no_dependents_is_noop(db) -> None:
    """Marking a task done with no dependents returns an empty woken list
    and does not error."""
    room = await _room(db)
    a, agent_a, _ = await _agent_with_task(db, agent_name="a", room=room)
    result = await mark_task_status(
        db, agent_id=agent_a.id, arguments={"task_id": a.id, "status": "done"}
    )
    assert result["isError"] is False
    assert result["structuredContent"]["woken"] == []


# ── end-to-end JSON-RPC (router wiring) ─────────────────────────────


@pytest_asyncio.fixture()
async def blocker_rpc_env():
    """A live FastAPI app + agent token with a dependent task and its
    blocker task, both assigned to the same agent, for end-to-end MCP RPC
    tests of the task_blockers tools + resolve-wake."""
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
        dep = Task(
            room_id=room.id,
            title="dependent",
            status="blocked",
            assignee_participant_id=p.id,
        )
        blocker = Task(
            room_id=room.id,
            title="blocker",
            status="todo",
            assignee_participant_id=p.id,
        )
        session.add_all([dep, blocker])
        await session.commit()
        agent_id = agent.id
        dep_id = dep.id
        blocker_id = blocker.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "token": plain,
            "agent_id": agent_id,
            "dep_id": dep_id,
            "blocker_id": blocker_id,
            "factory": factory,
        }
    await engine.dispose()


async def _rpc_tool(client, token, name, arguments):
    resp = await client.post(
        "/mcp/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp


@pytest.mark.asyncio
async def test_rpc_add_then_resolve_wake_persists(blocker_rpc_env) -> None:
    """End-to-end: add_task_blocker via RPC persists the edge; marking the
    blocker done via RPC clears the edge and returns the dependent to todo.
    Exercises the router wiring + commit boundary for both new branches."""
    env = blocker_rpc_env
    client, token = env["client"], env["token"]

    # 1. Add the blocker edge.
    r_add = await _rpc_tool(
        client,
        token,
        "add_task_blocker",
        {"task_id": env["dep_id"], "blocked_by_task_id": env["blocker_id"]},
    )
    assert r_add.status_code == 200
    assert r_add.json()["result"]["isError"] is False

    async with env["factory"]() as db2:
        edges = (
            await db2.execute(
                select(TaskBlocker).where(
                    TaskBlocker.task_id == env["dep_id"]
                )
            )
        ).scalars().all()
        assert len(edges) == 1

    # 2. Mark the blocker done — resolve-wake fires, dependent -> todo.
    r_done = await _rpc_tool(
        client, token, "mark_task_status",
        {"task_id": env["blocker_id"], "status": "done"},
    )
    assert r_done.status_code == 200
    body = r_done.json()["result"]
    assert body["isError"] is False
    assert body["structuredContent"]["woken"] == [env["dep_id"]]

    async with env["factory"]() as db2:
        dep = await db2.get(Task, env["dep_id"])
        assert dep.status == "todo"
        edges = (
            await db2.execute(
                select(TaskBlocker).where(
                    TaskBlocker.task_id == env["dep_id"]
                )
            )
        ).scalars().all()
        assert edges == []


@pytest.mark.asyncio
async def test_rpc_clear_task_blocker_persists(blocker_rpc_env) -> None:
    """End-to-end: clear_task_blocker via RPC removes the edge and the
    delete survives the request (commit happened)."""
    env = blocker_rpc_env
    client, token = env["client"], env["token"]
    args = {"task_id": env["dep_id"], "blocked_by_task_id": env["blocker_id"]}

    await _rpc_tool(client, token, "add_task_blocker", args)
    r_clear = await _rpc_tool(client, token, "clear_task_blocker", args)
    assert r_clear.status_code == 200
    assert r_clear.json()["result"]["isError"] is False

    async with env["factory"]() as db2:
        edges = (
            await db2.execute(
                select(TaskBlocker).where(
                    TaskBlocker.task_id == env["dep_id"]
                )
            )
        ).scalars().all()
        assert edges == []
