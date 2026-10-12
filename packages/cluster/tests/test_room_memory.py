"""Room memory REST router and machine-write guards (MEM-01..04, MEM-07)."""

from __future__ import annotations

import secrets
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentRoomMemory,
    Base,
    Machine,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.memory.service import (
    apply_machine_memory_update,
    get_room_memories,
    get_room_memory,
    update_room_memory,
)


class _RecordingBus:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, machine_id: str, frame: dict[str, Any]) -> bool:
        self.sent.append((machine_id, frame))
        return True


@pytest_asyncio.fixture()
async def memory_env():
    from cryptography.fernet import Fernet

    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
    )
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        admin = User(email="admin@example.com", password_hash="x", is_admin=True)
        db.add(admin)
        await db.flush()
        project_a = Project(name="proj-a")
        project_b = Project(name="proj-b")
        db.add_all([project_a, project_b])
        await db.flush()
        room_a = Room(project_id=project_a.id, name="room-a")
        room_b = Room(project_id=project_b.id, name="room-b")
        temp = Room(project_id=project_a.id, name="temp", ephemeral=True)
        db.add_all([room_a, room_b, temp])
        await db.flush()
        machine = Machine(id="m1", name="m1", hostname="localhost", owner_user_id=admin.id)
        db.add(machine)
        await db.flush()
        agent = Agent(name="agent-x", engine="codex", placed_on_machine_id="m1", generation=3)
        db.add(agent)
        await db.flush()
        for room in (room_a, room_b, temp):
            db.add(Participant(room_id=room.id, user_id=admin.id, role="admin"))
            db.add(Participant(room_id=room.id, agent_id=agent.id, role="member"))
        await db.commit()
        ids = {
            "agent": agent.id, "room_a": room_a.id, "room_b": room_b.id, "temp": temp.id,
        }

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = session_factory
    bus = _RecordingBus()
    app.state.machine_bus = bus  # type: ignore[assignment]
    token = create_user_token(admin.id, admin.email, True, secret=config.jwt_secret)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield {"client": client, "session_factory": session_factory, "bus": bus, **ids}
    await engine.dispose()


def _url(env: dict, room_key: str = "room_a") -> str:
    return f"/api/v1/agents/{env['agent']}/rooms/{env[room_key]}/memory"


def _detail(resp) -> dict:
    body = resp.json()
    return body.get("detail", body)


@pytest.mark.req("MEM-01")
async def test_stale_expected_revision_returns_409_with_current_snapshot(memory_env):
    client = memory_env["client"]
    first = await client.put(_url(memory_env), json={"expected_revision": 0, "memory_md": "v1"})
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1

    stale = await client.put(_url(memory_env), json={"expected_revision": 0, "memory_md": "lost"})
    assert stale.status_code == 409, stale.text
    detail = _detail(stale)
    assert detail["code"] == "MEMORY_REVISION_CONFLICT"
    assert detail["room_memory"]["memory_md"] == "v1"
    assert detail["room_memory"]["revision"] == 1

    current = await client.get(_url(memory_env))
    assert current.json()["memory_md"] == "v1"


@pytest.mark.req("MEM-04")
async def test_admin_edit_bumps_session_epoch(memory_env):
    client = memory_env["client"]
    before = (await client.get(_url(memory_env))).json()
    assert before["session_epoch"] == 0

    edited = await client.put(_url(memory_env), json={"expected_revision": 0, "memory_md": "admin"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["session_epoch"] == 1

    cleared = await client.post(f"{_url(memory_env)}/clear", json={"expected_revision": 1})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["session_epoch"] == 2
    assert cleared.json()["memory_md"] == ""

    # The placed daemon receives the new epoch so it rotates the session key.
    snapshots = [f for _, f in memory_env["bus"].sent if f["type"] == "agent_room_memory_snapshot"]
    assert snapshots and snapshots[-1]["room_memory"]["session_epoch"] == 2


@pytest.mark.req("MEM-04")
async def test_machine_write_keeps_session_epoch(memory_env):
    async with memory_env["session_factory"]() as db:
        snap = await update_room_memory(
            db, agent_id=memory_env["agent"], room_id=memory_env["room_a"], memory_md="agent",
            expected_revision=0, administrator=False, machine_id="m1", generation=3,
        )
        assert snap["revision"] == 1
        assert snap["session_epoch"] == 0


@pytest.mark.req("MEM-02")
async def test_agent_cannot_write_ephemeral_room_memory(memory_env):
    async with memory_env["session_factory"]() as db:
        ack = await apply_machine_memory_update(db, machine_id="m1", data={
            "agent_id": memory_env["agent"], "room_id": memory_env["temp"],
            "generation": 3, "base_revision": 0, "memory_md": "should not persist",
        })
        await db.commit()
    assert ack["status"] == "rejected"
    assert ack["code"] == "MEMORY_EPHEMERAL"
    async with memory_env["session_factory"]() as db:
        row = await db.get(AgentRoomMemory, (memory_env["agent"], memory_env["temp"]))
        assert row is None or row.memory_md == ""


@pytest.mark.req("MEM-03")
@pytest.mark.parametrize(
    ("machine_id", "generation"), [("m-other", 3), ("m1", 2)], ids=["wrong-machine", "old-generation"]
)
async def test_machine_write_from_stale_runtime_is_rejected(memory_env, machine_id, generation):
    async with memory_env["session_factory"]() as db:
        ack = await apply_machine_memory_update(db, machine_id=machine_id, data={
            "agent_id": memory_env["agent"], "room_id": memory_env["room_a"],
            "generation": generation, "base_revision": 0, "memory_md": "stale",
        })
    assert ack["status"] == "rejected"
    assert ack["code"] == "MEMORY_RUNTIME_STALE"
    async with memory_env["session_factory"]() as db:
        assert (await get_room_memory(db, memory_env["agent"], memory_env["room_a"]))["memory_md"] == ""


@pytest.mark.req("MEM-07")
async def test_room_memory_does_not_leak_across_projects(memory_env):
    client = memory_env["client"]
    resp = await client.put(_url(memory_env), json={"expected_revision": 0, "memory_md": "project A secret"})
    assert resp.status_code == 200, resp.text

    other = (await client.get(_url(memory_env, "room_b"))).json()
    assert other["memory_md"] == ""
    assert other["revision"] == 0

    async with memory_env["session_factory"]() as db:
        manifest = await get_room_memories(
            db, memory_env["agent"], [memory_env["room_a"], memory_env["room_b"]]
        )
    assert manifest[memory_env["room_a"]]["memory_md"] == "project A secret"
    assert manifest[memory_env["room_b"]]["memory_md"] == ""
