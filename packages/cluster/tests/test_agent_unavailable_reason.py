"""Write-path tests for the structured agent unavailability reason (#516).

Covers where ``scheduler/lifecycle.py`` stamps and clears
``Agent.unavailable_code`` / ``unavailable_detail`` / ``unavailable_since``:
no_machine, no_room, engine drift, crash vs spawn failure, and the clear
paths (successful placement, running, intentional stop).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from anygarden.agent_availability import (
    CRASHED,
    ENGINE_MISMATCH,
    NO_MACHINE_FOR_ENGINE,
    NO_ROOM,
    SPAWN_FAILED,
)
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    ActivityLog,
    Agent,
    Base,
    Machine,
    MachineEngine,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


@pytest_asyncio.fixture()
async def env():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    async with factory() as db:
        user = User(email="u@test.com", password_hash="x")
        db.add(user)
        await db.flush()
        machine = Machine(
            name="m", hostname="h", owner_user_id=user.id,
            status="online", max_agents=5,
        )
        db.add(machine)
        await db.flush()
        db.add(MachineEngine(machine_id=machine.id, engine="echo"))
        project = Project(name="p")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="r")
        db.add(room)
        await db.commit()
        await bus.register(machine.id, _FakeWS())
        machine_id, room_id = machine.id, room.id

    async def make_agent(engine_name: str, *, in_room: bool = True, **kw) -> str:
        async with factory() as db:
            agent = Agent(
                name="a", engine=engine_name,
                desired_state="running", actual_state="pending", **kw,
            )
            db.add(agent)
            await db.flush()
            if in_room:
                db.add(Participant(room_id=room_id, agent_id=agent.id, role="member"))
            await db.commit()
            return agent.id

    async def get_agent(agent_id: str) -> Agent:
        async with factory() as db:
            return (await db.execute(
                select(Agent).where(Agent.id == agent_id)
            )).scalar_one()

    async def activity_codes(agent_id: str) -> list[str]:
        async with factory() as db:
            rows = (await db.execute(
                select(ActivityLog).where(
                    ActivityLog.agent_id == agent_id,
                    ActivityLog.event_type == "agent_unavailable",
                )
            )).scalars().all()
            return [r.details.get("code") for r in rows]

    yield {
        "factory": factory, "lifecycle": lifecycle, "bus": bus,
        "machine_id": machine_id, "room_id": room_id,
        "make_agent": make_agent, "get_agent": get_agent,
        "activity_codes": activity_codes,
    }
    await engine.dispose()


@pytest.mark.asyncio
async def test_no_machine_stamps_reason_and_releases_placement(env) -> None:
    # engine with no supporting online machine
    aid = await env["make_agent"]("nonexistent-engine")
    await env["lifecycle"].request_start(aid)

    agent = await env["get_agent"](aid)
    assert agent.actual_state == "pending"
    assert agent.unavailable_code == NO_MACHINE_FOR_ENGINE
    assert agent.unavailable_detail == {"engine": "nonexistent-engine"}
    assert agent.unavailable_since is not None
    # placement released so a newly-registered machine can adopt it
    assert agent.placed_on_machine_id is None
    # audit trail
    assert NO_MACHINE_FOR_ENGINE in await env["activity_codes"](aid)


@pytest.mark.asyncio
async def test_no_room_stamps_reason(env) -> None:
    aid = await env["make_agent"]("echo", in_room=False)
    await env["lifecycle"].request_start(aid)
    agent = await env["get_agent"](aid)
    assert agent.unavailable_code == NO_ROOM


@pytest.mark.asyncio
async def test_successful_placement_clears_prior_reason(env) -> None:
    aid = await env["make_agent"]("echo")
    # pre-stamp a stale reason as if a prior no_machine happened
    async with env["factory"]() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == aid))).scalar_one()
        agent.unavailable_code = NO_MACHINE_FOR_ENGINE
        agent.unavailable_detail = {"engine": "echo"}
        await db.commit()

    await env["lifecycle"].request_start(aid)  # machine "echo" exists → success
    agent = await env["get_agent"](aid)
    assert agent.placed_on_machine_id is not None
    assert agent.unavailable_code is None
    assert agent.unavailable_detail is None
    assert agent.unavailable_since is None


@pytest.mark.asyncio
async def test_report_running_matching_engine_clears(env) -> None:
    aid = await env["make_agent"]("echo", placed_on_machine_id=env["machine_id"])
    # stale reason present
    async with env["factory"]() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == aid))).scalar_one()
        agent.unavailable_code = SPAWN_FAILED
        await db.commit()

    await env["lifecycle"].handle_report_actual_state(
        env["machine_id"],
        [{"agent_id": aid, "actual_state": "running", "engine": "echo", "pid": 1}],
    )
    agent = await env["get_agent"](aid)
    assert agent.unavailable_code is None


@pytest.mark.asyncio
async def test_report_running_engine_drift_flags_mismatch(env) -> None:
    # DB says codex-cli (post-migration) but the live process is still codex
    aid = await env["make_agent"]("codex-cli", placed_on_machine_id=env["machine_id"])
    await env["lifecycle"].handle_report_actual_state(
        env["machine_id"],
        [{"agent_id": aid, "actual_state": "running", "engine": "codex", "pid": 1}],
    )
    agent = await env["get_agent"](aid)
    assert agent.unavailable_code == ENGINE_MISMATCH
    assert agent.unavailable_detail == {
        "db_engine": "codex-cli", "running_engine": "codex",
    }


@pytest.mark.asyncio
async def test_report_crashed_with_uptime_is_crashed(env) -> None:
    aid = await env["make_agent"]("echo", placed_on_machine_id=env["machine_id"])
    await env["lifecycle"].handle_report_actual_state(
        env["machine_id"],
        [{
            "agent_id": aid, "actual_state": "crashed", "engine": "echo",
            "uptime_seconds": 42, "last_crash_reason": "boom",
        }],
    )
    agent = await env["get_agent"](aid)
    assert agent.unavailable_code == CRASHED
    assert agent.unavailable_detail["stderr_tail"] == "boom"


@pytest.mark.asyncio
async def test_report_crashed_zero_uptime_is_spawn_failed(env) -> None:
    aid = await env["make_agent"]("echo", placed_on_machine_id=env["machine_id"])
    await env["lifecycle"].handle_report_actual_state(
        env["machine_id"],
        [{
            "agent_id": aid, "actual_state": "crashed", "engine": "echo",
            "uptime_seconds": 0, "last_crash_reason": "Unknown engine 'echo'",
        }],
    )
    agent = await env["get_agent"](aid)
    assert agent.unavailable_code == SPAWN_FAILED


@pytest.mark.asyncio
async def test_request_stop_clears_reason(env) -> None:
    aid = await env["make_agent"]("echo", placed_on_machine_id=env["machine_id"])
    async with env["factory"]() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == aid))).scalar_one()
        agent.unavailable_code = CRASHED
        await db.commit()

    await env["lifecycle"].request_stop(aid)
    agent = await env["get_agent"](aid)
    assert agent.unavailable_code is None
    assert agent.desired_state == "stopped"


@pytest.mark.asyncio
async def test_agent_out_exposes_admin_reason(env) -> None:
    from anygarden.api.v1.agents import _agent_to_out

    aid = await env["make_agent"]("nonexistent-engine")
    await env["lifecycle"].request_start(aid)  # → no_machine reason stamped
    agent = await env["get_agent"](aid)

    out = _agent_to_out(agent, machine_bus=None)
    assert out.unavailable_reason is not None
    assert out.unavailable_reason.code == NO_MACHINE_FOR_ENGINE
    assert "nonexistent-engine" in out.unavailable_reason.message
    assert out.unavailable_reason.detail == {"engine": "nonexistent-engine"}


@pytest.mark.asyncio
async def test_agent_out_reason_none_when_fine(env) -> None:
    from anygarden.api.v1.agents import _agent_to_out

    aid = await env["make_agent"]("echo")  # no unavailable_code stamped
    agent = await env["get_agent"](aid)
    out = _agent_to_out(agent, machine_bus=None)
    assert out.unavailable_reason is None


@pytest.mark.asyncio
async def test_unavailable_since_preserved_across_same_code(env) -> None:
    aid = await env["make_agent"]("echo", placed_on_machine_id=env["machine_id"])
    report = [{
        "agent_id": aid, "actual_state": "crashed", "engine": "echo",
        "uptime_seconds": 5, "last_crash_reason": "boom",
    }]
    await env["lifecycle"].handle_report_actual_state(env["machine_id"], report)
    first = (await env["get_agent"](aid)).unavailable_since
    # a second identical crash report must not reset the "since" clock
    await env["lifecycle"].handle_report_actual_state(env["machine_id"], report)
    second = (await env["get_agent"](aid)).unavailable_since
    assert first == second


class _FakeManager:
    def __init__(self) -> None:
        self.broadcasts: list = []

    async def broadcast(self, room_id: str, frame) -> None:
        self.broadcasts.append((room_id, frame))


@pytest.mark.asyncio
async def test_reactive_notice_posts_and_debounces(env) -> None:
    from datetime import datetime, timezone

    from anygarden.ws.handler import (
        _UNAVAIL_NOTICE_SEEN,
        _notify_unavailable_responders,
    )

    _UNAVAIL_NOTICE_SEEN.clear()
    manager = _FakeManager()
    since = datetime(2026, 7, 3, tzinfo=timezone.utc)
    responder = {
        "agent_id": "ag-1", "name": "Nova",
        "code": NO_MACHINE_FOR_ENGINE, "detail": {"engine": "codex-cli"},
        "since": since,
    }

    # first send → one notice, naming the agent, mentioning the engine
    await _notify_unavailable_responders(
        env["factory"], manager, env["room_id"], [responder]
    )
    assert len(manager.broadcasts) == 1
    assert "Nova" in manager.broadcasts[0][1].content
    assert "codex-cli" in manager.broadcasts[0][1].content

    # second send, same (agent, since) → debounced, no new notice
    await _notify_unavailable_responders(
        env["factory"], manager, env["room_id"], [responder]
    )
    assert len(manager.broadcasts) == 1

    # a fresh reason (new since) → notice again
    responder2 = {**responder, "since": datetime(2026, 7, 4, tzinfo=timezone.utc)}
    await _notify_unavailable_responders(
        env["factory"], manager, env["room_id"], [responder2]
    )
    assert len(manager.broadcasts) == 2
    _UNAVAIL_NOTICE_SEEN.clear()
