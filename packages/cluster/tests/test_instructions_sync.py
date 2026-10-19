"""#908 — server side of disk-sourced agent instructions."""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentFile,
    AgentInstructionRevision,
    Base,
    Machine,
    User,
)
from anygarden.instructions import service
from anygarden.machine import instructions as machine_instructions
from anygarden.scheduler.machine_bus import MachineBus, MachineRequestError


class FakeBus(MachineBus):
    def __init__(self, connected: bool = True) -> None:
        super().__init__()
        self.connected = connected
        self.sent: list[dict] = []
        self.requests: list[dict] = []
        self.reply: dict | Exception = {}

    def is_connected(self, machine_id: str) -> bool:
        return self.connected

    async def send(self, machine_id: str, frame: dict) -> bool:
        self.sent.append(frame)
        return True

    async def request_instructions(self, machine_id: str, **values) -> dict:
        self.requests.append(values)
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"request_id": "r", "agent_id": values["agent_id"], **self.reply}


@pytest_asyncio.fixture()
async def env():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user = User(email="i@test.com", password_hash="x", is_admin=True)
        db.add(user)
        await db.flush()
        disk = Machine(name="disk", hostname="d", owner_user_id=user.id, status="online",
                       control_capabilities=[service.CAPABILITY])
        legacy = Machine(name="legacy", hostname="l", owner_user_id=user.id, status="online")
        db.add_all([disk, legacy])
        await db.flush()
        agent = Agent(name="a", engine="codex-cli", agents_md="v0",
                      placed_on_machine_id=disk.id, instructions_revision=3)
        db.add(agent)
        await db.flush()
        db.add(AgentFile(agent_id=agent.id, path="skills/a/SKILL.md", content="A"))
        db.add(AgentFile(agent_id=agent.id, path=".codex/config.toml", content="x=1"))
        await db.commit()
        ids = {"agent": agent.id, "disk": disk.id, "legacy": legacy.id, "user": user.id}
    yield factory, ids
    await engine.dispose()


async def _files(db, agent_id) -> dict[str, str]:
    rows = (await db.scalars(select(AgentFile).where(AgentFile.agent_id == agent_id))).all()
    return {row.path: row.content for row in rows}


def test_capability_matches_the_machine_module() -> None:
    assert service.CAPABILITY == machine_instructions.CAPABILITY


@pytest.mark.req("AGT-11")
@pytest.mark.asyncio
async def test_machine_snapshot_becomes_the_next_revision(env) -> None:
    factory, ids = env
    async with factory() as db:
        ack = await service.apply_machine_update(db, machine_id=ids["disk"], data={
            "type": "agent_instructions_update", "agent_id": ids["agent"], "generation": 1,
            "base_revision": 3, "digest": "d1",
            "snapshot": {"agents_md": "agent rule", "skills": {"skills/b/SKILL.md": "B"}},
        })
        await db.commit()
        assert ack == {"type": "agent_instructions_ack", "agent_id": ids["agent"],
                       "status": "accepted", "revision": 4, "digest": "d1"}
        agent = await db.get(Agent, ids["agent"])
        assert agent.agents_md == "agent rule" and agent.instructions_revision == 4
        # Engine settings are not instructions and stay untouched.
        assert await _files(db, ids["agent"]) == {"skills/b/SKILL.md": "B", ".codex/config.toml": "x=1"}
        revision = (await db.scalars(select(AgentInstructionRevision))).one()
        assert (revision.revision, revision.source, revision.skills) == (4, "agent", {"skills/b/SKILL.md": "B"})


@pytest.mark.asyncio
async def test_stale_or_foreign_snapshots_are_not_recorded(env) -> None:
    factory, ids = env
    frame = {"type": "agent_instructions_update", "agent_id": ids["agent"], "generation": 1,
             "base_revision": 2, "digest": "d", "snapshot": {"agents_md": "late", "skills": {}}}
    async with factory() as db:
        ack = await service.apply_machine_update(db, machine_id=ids["disk"], data=frame)
        assert ack["status"] == "rejected" and ack["revision"] == 3
        assert ack["snapshot"] == {"agents_md": "v0", "skills": {"skills/a/SKILL.md": "A"}}
        assert await service.apply_machine_update(db, machine_id=ids["legacy"], data=frame) is None
        assert (await db.get(Agent, ids["agent"])).agents_md == "v0"


@pytest.mark.req("AGT-11")
@pytest.mark.asyncio
async def test_disk_write_records_the_machine_reply_and_acks(env) -> None:
    factory, ids = env
    bus = FakeBus()
    bus.reply = {"status": "ok", "base_revision": 3, "digest": "d2",
                 "snapshot": {"agents_md": "person rule", "skills": {"skills/a/SKILL.md": "A"}}}
    async with factory() as db:
        agent = await db.get(Agent, ids["agent"])
        revision = await service.write_instructions(
            db, bus, agent, operation="write_agents_md", text="person rule", by_user_id=ids["user"])
        assert revision == 4
        assert bus.requests[0]["base_revision"] == 3
        assert bus.sent == [{"type": "agent_instructions_ack", "agent_id": ids["agent"],
                             "status": "accepted", "revision": 4, "digest": "d2"}]
        stored = (await db.scalars(select(AgentInstructionRevision))).one()
        assert (stored.source, stored.by_user_id) == ("user", ids["user"])


@pytest.mark.asyncio
async def test_disk_write_refusals(env) -> None:
    factory, ids = env
    async with factory() as db:
        agent = await db.get(Agent, ids["agent"])
        offline = FakeBus(connected=False)
        with pytest.raises(HTTPException) as caught:
            await service.write_instructions(db, offline, agent, operation="write_agents_md",
                                             text="x", by_user_id=None)
        assert caught.value.status_code == 409 and "offline" in caught.value.detail

        conflict = FakeBus()
        conflict.reply = {"status": "conflict", "base_revision": 3}
        with pytest.raises(HTTPException) as caught:
            await service.write_instructions(db, conflict, agent, operation="write_agents_md",
                                             text="x", by_user_id=None)
        assert caught.value.status_code == 409

        silent = FakeBus()
        silent.reply = MachineRequestError("timeout")
        with pytest.raises(HTTPException):
            await service.write_instructions(db, silent, agent, operation="write_agents_md",
                                             text="x", by_user_id=None)

        with pytest.raises(HTTPException) as caught:
            await service.write_instructions(db, FakeBus(), agent, operation="write_skill",
                                             path=".codex/config.toml", text="x", by_user_id=None)
        assert caught.value.status_code == 400
        assert (await db.get(Agent, ids["agent"])).instructions_revision == 3


@pytest.mark.asyncio
async def test_server_mode_edits_the_snapshot(env) -> None:
    factory, ids = env
    async with factory() as db:
        agent = await db.get(Agent, ids["agent"])
        agent.placed_on_machine_id = ids["legacy"]
        await db.commit()
        bus = FakeBus()
        await service.write_instructions(db, bus, agent, operation="write_skill",
                                         path="skills/c/SKILL.md", text="C", by_user_id=None)
        await service.write_instructions(db, bus, agent, operation="delete_skill",
                                         path="skills/a/SKILL.md", by_user_id=None)
        assert bus.requests == []
        assert agent.instructions_revision == 5
        assert await _files(db, ids["agent"]) == {"skills/c/SKILL.md": "C", ".codex/config.toml": "x=1"}


@pytest.mark.asyncio
async def test_unseeded_machine_falls_back_to_the_snapshot(env) -> None:
    factory, ids = env
    bus = FakeBus()
    bus.reply = {"status": "no_receipt"}
    async with factory() as db:
        agent = await db.get(Agent, ids["agent"])
        await service.write_instructions(db, bus, agent, operation="write_agents_md",
                                         text="seed me", by_user_id=None)
        assert agent.agents_md == "seed me" and bus.sent == []


@pytest.mark.asyncio
async def test_only_the_latest_revisions_are_kept(env, monkeypatch) -> None:
    factory, ids = env
    monkeypatch.setattr(service, "INSTRUCTION_REVISIONS_KEPT", 3)
    async with factory() as db:
        agent = await db.get(Agent, ids["agent"])
        for index in range(5):
            await service.record_snapshot(db, agent, service.Snapshot(f"v{index}", {}), source="user")
        await db.commit()
        kept = [row.revision for row in await service.list_revisions(db, ids["agent"])]
        assert kept == [8, 7, 6]


def test_modes() -> None:
    disk = Machine(id="m", name="m", hostname="h", owner_user_id="u",
                   control_capabilities=[service.CAPABILITY])
    legacy = Machine(id="m", name="m", hostname="h", owner_user_id="u", control_capabilities=[])
    agent = Agent(name="a", engine="codex-cli", placed_on_machine_id="m")
    assert service.instructions_mode(agent, disk, FakeBus()) == "disk"
    assert service.instructions_mode(agent, disk, FakeBus(connected=False)) == "disk_offline"
    assert service.instructions_mode(agent, legacy, FakeBus()) == "server"
    assert service.instructions_mode(Agent(name="b", engine="x"), None, FakeBus()) == "server"
